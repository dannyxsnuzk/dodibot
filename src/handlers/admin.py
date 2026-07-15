"""In-bot admin commands. Admin IDs come from settings.admin_ids."""
from __future__ import annotations

import contextlib
import logging
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Document, Message
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import Order, PaymentVerification, Product, StockItem, Transaction, User, Withdrawal
from ..repositories import payments as payments_repo
from ..repositories import products as products_repo
from ..repositories import withdrawals as wd_repo
from ..repositories.users import get_user
from ..services.shop import BuyError, OutOfStock, buy_product_quantity
from ..services import wallet
from ..ui import keyboards as kb
from ..ui import texts
from ..ui.emoji import pe, reload_map, list_loaded_keys, validate_registry
from .states import AdminStates

router = Router(name="admin")
log = logging.getLogger(__name__)


def is_admin(user_id: int) -> bool:
    return user_id in get_settings().admin_ids


@router.message(Command("admin"))
async def admin_help(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "<b>Admin commands</b>\n\n"
        "<b>Stock &amp; products</b>\n"
        "<code>/products</code> — list products with stock\n"
        "<code>/addproduct slug|Name|emoji|duration|price</code>\n"
        "<code>/setprice slug 24.99</code>\n"
        "<code>/setactive slug on|off</code>\n"
        "<code>/setdesc slug Description text...</code>\n"
        "<code>/setemoji slug ID</code> — bind a premium custom_emoji_id (or <code>clear</code>)\n"
        "<code>/addstock slug</code> — then send a .txt with one credential per line\n"
        "<code>/stock slug</code> — show available stock count\n"
        "<code>/clearstock slug confirm</code> — delete all unsold stock\n"
        "<code>/delproduct slug confirm</code> — hard-delete a product (must have no orders)\n\n"
        "<b>Withdrawals</b>\n"
        "<code>/withdrawals</code> — pending\n"
        "<code>/approve_wd ID</code>\n"
        "<code>/reject_wd ID reason</code>\n\n"
        "<b>Users</b>\n"
        "<code>/whois USER_ID</code>\n"
        "<code>/credit USER_ID 10.00 reason</code>\n"
        "<code>/debit USER_ID 5.00 reason</code>\n"
        "<code>/ban USER_ID</code> / <code>/unban USER_ID</code>\n\n"
        "<b>Other</b>\n"
        "<code>/payments [status]</code> — payment verification audit\n"
        "<code>/approve_pay ID [note]</code>\n"
        "<code>/reject_pay ID reason</code>\n"
        "<code>/broadcast</code> — then send the message to broadcast\n"
        "<code>/getemoji</code> — reply to a message containing premium emojis to capture their IDs\n"
        "<code>/reload_emojis</code> — re-read assets/premium_emojis.json\n"
        "<code>/stats</code> — bot stats",
        parse_mode="HTML",
    )


# ─── Products ─────────────────────────────────────────────────────────────────

@router.message(Command("products"))
async def list_products(message: Message, session: AsyncSession) -> None:
    if not is_admin(message.from_user.id):
        return
    items = await products_repo.list_active_products_with_stock(session)
    if not items:
        await message.answer("No products yet.")
        return
    lines = []
    for p, stock in items:
        active = pe("check") if p.is_active else pe("disabled")
        lines.append(
            f"{active} <b>{p.slug}</b> · {p.emoji} {p.display_name} {p.duration_label} · "
            f"{p.price_usdt:.2f} USDT · stock {stock}"
        )
    # Also show inactive
    inactive = (await session.scalars(
        select(Product).where(Product.is_active.is_(False))
    )).all()
    for p in inactive:
        lines.append(f"{pe('disabled')} <b>{p.slug}</b> · {p.emoji} {p.display_name} {p.duration_label} · {p.price_usdt:.2f} USDT (inactive)")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("addproduct"))
async def addproduct(message: Message, command: CommandObject, session: AsyncSession) -> None:
    if not is_admin(message.from_user.id):
        return
    args = (command.args or "").strip()
    parts = [p.strip() for p in args.split("|")]
    if len(parts) < 5:
        await message.answer("Usage: /addproduct slug|Name|emoji|duration|price")
        return
    slug, name, emoji, duration, price_str = parts[:5]
    try:
        price = Decimal(price_str)
    except InvalidOperation:
        await message.answer("Invalid price.")
        return
    p = await products_repo.upsert_product(
        session,
        slug=slug, display_name=name, emoji=emoji,
        duration_label=duration, price_usdt=price,
    )
    await message.answer(f"Saved: <b>{p.slug}</b> · {p.emoji} {p.display_name} {p.duration_label} · {p.price_usdt:.2f} USDT",
                         parse_mode="HTML")


@router.message(Command("setprice"))
async def setprice(message: Message, command: CommandObject, session: AsyncSession) -> None:
    if not is_admin(message.from_user.id):
        return
    parts = (command.args or "").split()
    if len(parts) != 2:
        await message.answer("Usage: /setprice slug 24.99")
        return
    slug, price_str = parts
    try:
        price = Decimal(price_str)
    except InvalidOperation:
        await message.answer("Invalid price.")
        return
    p = await session.scalar(select(Product).where(Product.slug == slug))
    if p is None:
        await message.answer("Product not found.")
        return
    p.price_usdt = price
    await session.commit()
    await message.answer(f"Updated price for <b>{slug}</b> → {price:.2f} USDT", parse_mode="HTML")


@router.message(Command("setactive"))
async def setactive(message: Message, command: CommandObject, session: AsyncSession) -> None:
    if not is_admin(message.from_user.id):
        return
    parts = (command.args or "").split()
    if len(parts) != 2:
        await message.answer("Usage: /setactive slug on|off")
        return
    slug, mode = parts
    p = await session.scalar(select(Product).where(Product.slug == slug))
    if p is None:
        await message.answer("Product not found.")
        return
    p.is_active = mode.lower() == "on"
    await session.commit()
    await message.answer(f"<b>{slug}</b> is now {'ACTIVE' if p.is_active else 'INACTIVE'}", parse_mode="HTML")


@router.message(Command("setdesc"))
async def setdesc(message: Message, command: CommandObject, session: AsyncSession) -> None:
    if not is_admin(message.from_user.id):
        return
    args = (command.args or "")
    parts = args.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: /setdesc slug description text")
        return
    slug, desc = parts
    p = await session.scalar(select(Product).where(Product.slug == slug))
    if p is None:
        await message.answer("Product not found.")
        return
    p.description = desc
    await session.commit()
    await message.answer(f"Description updated for <b>{slug}</b>", parse_mode="HTML")


@router.message(Command("setemoji"))
async def setemoji(message: Message, command: CommandObject, session: AsyncSession) -> None:
    """Set or clear the premium custom_emoji_id for a product.

    Usage:
        /setemoji slug 5368324170671202286   — bind a premium emoji
        /setemoji slug clear                  — drop the premium binding
    """
    if not is_admin(message.from_user.id):
        return
    parts = (command.args or "").split()
    if len(parts) < 2:
        await message.answer(
            "Usage: <code>/setemoji slug ID</code> or <code>/setemoji slug clear</code>\n\n"
            "Use <code>/getemoji</code> by replying to a message with premium emojis "
            "to capture their IDs.",
            parse_mode="HTML",
        )
        return
    slug, val = parts[0], parts[1]
    p = await session.scalar(select(Product).where(Product.slug == slug))
    if p is None:
        await message.answer("Product not found.")
        return
    if val.lower() == "clear":
        p.emoji_id = None
        await session.commit()
        await message.answer(f"Cleared premium emoji for <b>{slug}</b>.", parse_mode="HTML")
        return
    if not val.isdigit():
        await message.answer(
            "Premium emoji ID must be a number. Use <code>/getemoji</code> to fetch it.",
            parse_mode="HTML",
        )
        return
    p.emoji_id = val
    await session.commit()
    await message.answer(
        f"Premium emoji set for <b>{slug}</b>: "
        f'<tg-emoji emoji-id="{val}">{p.emoji}</tg-emoji>',
        parse_mode="HTML",
    )


@router.message(Command("addstock"))
async def addstock_cmd(message: Message, command: CommandObject, state: FSMContext, session: AsyncSession) -> None:
    if not is_admin(message.from_user.id):
        return
    slug = (command.args or "").strip()
    if not slug:
        await message.answer("Usage: /addstock slug — then send a .txt file with one credential per line.")
        return
    p = await session.scalar(select(Product).where(Product.slug == slug))
    if p is None:
        await message.answer("Product not found.")
        return
    await state.set_state(AdminStates.waiting_stock_upload)
    await state.update_data(product_id=p.id, slug=slug)
    await message.answer(
        f"Send the stock as a <b>.txt file</b> (one credential per line) or as a plain message. "
        f"Adding to <b>{slug}</b>.",
        parse_mode="HTML",
    )


@router.message(AdminStates.waiting_stock_upload)
async def addstock_upload(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    pid = int(data["product_id"])
    slug = data.get("slug", "?")
    lines: list[str] = []
    if isinstance(message.document, Document):
        # Download the file
        f = await message.bot.get_file(message.document.file_id)
        bio = await message.bot.download_file(f.file_path)
        text = bio.read().decode("utf-8", errors="replace")
        lines = text.splitlines()
    elif message.text:
        lines = message.text.splitlines()
    else:
        await message.answer("Send a .txt file or paste lines.")
        return
    n = await products_repo.add_stock_lines(session, pid, lines)
    await state.clear()
    await message.answer(f"Added <b>{n}</b> stock items to <b>{slug}</b>.", parse_mode="HTML")


@router.message(Command("stock"))
async def stock_count(message: Message, command: CommandObject, session: AsyncSession) -> None:
    if not is_admin(message.from_user.id):
        return
    slug = (command.args or "").strip()
    p = await session.scalar(select(Product).where(Product.slug == slug))
    if p is None:
        await message.answer("Product not found.")
        return
    n = await products_repo.count_available_stock(session, p.id)
    await message.answer(f"<b>{slug}</b>: {n} available", parse_mode="HTML")


@router.message(Command("clearstock"))
async def clearstock(message: Message, command: CommandObject, session: AsyncSession) -> None:
    if not is_admin(message.from_user.id):
        return
    parts = (command.args or "").split()
    if not parts:
        await message.answer(
            "Usage: <code>/clearstock slug confirm</code>\n"
            "Removes all <b>unsold</b> stock items for the product. "
            "Sold items stay so order history is preserved.",
            parse_mode="HTML",
        )
        return
    slug = parts[0]
    confirm = parts[1].lower() if len(parts) > 1 else ""
    p = await session.scalar(select(Product).where(Product.slug == slug))
    if p is None:
        await message.answer("Product not found.")
        return
    n_avail = await products_repo.count_available_stock(session, p.id)
    if confirm != "confirm":
        await message.answer(
            f"<b>{slug}</b> has {n_avail} unsold stock items. "
            f"Run <code>/clearstock {slug} confirm</code> to delete them.",
            parse_mode="HTML",
        )
        return
    deleted = await products_repo.clear_available_stock(session, p.id)
    await message.answer(f"Removed <b>{deleted}</b> unsold stock items from <b>{slug}</b>.",
                         parse_mode="HTML")


@router.message(Command("delproduct"))
async def delproduct(message: Message, command: CommandObject, session: AsyncSession) -> None:
    if not is_admin(message.from_user.id):
        return
    parts = (command.args or "").split()
    if not parts:
        await message.answer(
            "Usage: <code>/delproduct slug confirm</code>\n"
            "Hard-deletes the product. Refuses if there is any order history "
            "(use <code>/setactive slug off</code> to hide instead).",
            parse_mode="HTML",
        )
        return
    slug = parts[0]
    confirm = parts[1].lower() if len(parts) > 1 else ""
    p = await session.scalar(select(Product).where(Product.slug == slug))
    if p is None:
        await message.answer("Product not found.")
        return
    n_orders = await products_repo.count_orders(session, p.id)
    n_avail = await products_repo.count_available_stock(session, p.id)
    if confirm != "confirm":
        await message.answer(
            f"<b>{slug}</b> · {p.emoji} {p.display_name}\n"
            f"Orders on record: <b>{n_orders}</b>\n"
            f"Unsold stock items: <b>{n_avail}</b>\n\n"
            + (f"{pe('warning')} Cannot delete — has order history. Use "
               f"<code>/setactive {slug} off</code> to hide instead.\n"
               if n_orders > 0
               else f"Run <code>/delproduct {slug} confirm</code> to permanently delete."),
            parse_mode="HTML",
        )
        return
    ok, msg = await products_repo.delete_product(session, p.id)
    await message.answer(msg)


# ─── Withdrawals ──────────────────────────────────────────────────────────────

@router.message(Command("withdrawals"))
async def list_withdrawals(message: Message, session: AsyncSession) -> None:
    if not is_admin(message.from_user.id):
        return
    pending = await wd_repo.get_pending_withdrawals(session)
    if not pending:
        await message.answer("No pending withdrawals.")
        return
    lines = []
    for w in pending:
        lines.append(
            f"#{w.id} · user <code>{w.user_id}</code> · {w.method.upper()} · "
            f"{w.amount_usdt:.2f} USDT → <code>{w.address}</code>"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("approve_wd"))
async def approve_wd(message: Message, command: CommandObject, session: AsyncSession) -> None:
    if not is_admin(message.from_user.id):
        return
    args = (command.args or "").split(maxsplit=1)
    if not args:
        await message.answer("Usage: /approve_wd ID [note]")
        return
    try:
        wid = int(args[0])
    except ValueError:
        await message.answer("Bad ID.")
        return
    w = await wd_repo.get_withdrawal(session, wid)
    if w is None or w.status != "pending":
        await message.answer("Not pending or not found.")
        return
    note = args[1] if len(args) > 1 else ""
    # Funds were already held when user submitted the request; just record the decision.
    await wd_repo.decide_withdrawal(
        session, withdrawal=w, approved=True, admin_id=message.from_user.id, admin_note=note,
        commit=False,
    )
    session.add(Transaction(
        user_id=w.user_id, kind="withdrawal", amount_usdt=-Decimal(str(w.amount_usdt)),
        ref_id=w.id, note=f"withdrawal#{w.id} approved",
    ))
    await session.commit()
    await message.answer(f"Approved withdrawal #{w.id}.")
    with contextlib.suppress(Exception):
        await message.bot.send_message(
            w.user_id,
            f"{pe('check')} <b>Withdrawal paid</b>\n\nTicket #{w.id} — {w.amount_usdt:.2f} USDT to <code>{w.address}</code>.",
            parse_mode="HTML",
        )


@router.message(Command("reject_wd"))
async def reject_wd(message: Message, command: CommandObject, session: AsyncSession) -> None:
    if not is_admin(message.from_user.id):
        return
    args = (command.args or "").split(maxsplit=1)
    if not args:
        await message.answer("Usage: /reject_wd ID [reason]")
        return
    try:
        wid = int(args[0])
    except ValueError:
        await message.answer("Bad ID.")
        return
    w = await wd_repo.get_withdrawal(session, wid)
    if w is None or w.status != "pending":
        await message.answer("Not pending or not found.")
        return
    note = args[1] if len(args) > 1 else ""
    # Refund the held funds
    await wallet.credit(
        session, user_id=w.user_id, amount=Decimal(str(w.amount_usdt)),
        kind="withdrawal_refund", ref_id=w.id,
        note=f"withdrawal#{w.id} rejected: {note}",
        commit=False,
    )
    await wd_repo.decide_withdrawal(
        session, withdrawal=w, approved=False, admin_id=message.from_user.id, admin_note=note,
        commit=False,
    )
    await session.commit()
    await message.answer(f"Rejected withdrawal #{w.id} — {w.amount_usdt:.2f} USDT refunded.")
    with contextlib.suppress(Exception):
        await message.bot.send_message(
            w.user_id,
            f"{pe('cross')} <b>Withdrawal rejected</b>\n\nTicket #{w.id} — {w.amount_usdt:.2f} USDT refunded.\n"
            f"Reason: {note or 'not specified'}",
            parse_mode="HTML",
        )


# ─── Users ────────────────────────────────────────────────────────────────────

@router.message(Command("whois"))
async def whois(message: Message, command: CommandObject, session: AsyncSession) -> None:
    if not is_admin(message.from_user.id):
        return
    try:
        uid = int((command.args or "").strip())
    except ValueError:
        await message.answer("Usage: /whois USER_ID")
        return
    u = await get_user(session, uid)
    if u is None:
        await message.answer("Not found.")
        return
    n_orders = int(await session.scalar(
        select(func.count(Order.id)).where(Order.user_id == uid)
    ) or 0)
    await message.answer(
        f"<b>User {u.id}</b> @{u.username or '-'}\n"
        f"Balance: {u.balance_usdt:.2f} USDT\n"
        f"Joined: {u.joined_at.strftime('%Y-%m-%d')}\n"
        f"Orders: {n_orders}\n"
        f"Banned: {u.is_banned}\n"
        f"Referral code: {u.referral_code}",
        parse_mode="HTML",
    )


@router.message(Command("credit"))
async def credit_user(message: Message, command: CommandObject, session: AsyncSession) -> None:
    if not is_admin(message.from_user.id):
        return
    parts = (command.args or "").split(maxsplit=2)
    if len(parts) < 2:
        await message.answer("Usage: /credit USER_ID 10.00 [note]")
        return
    try:
        uid = int(parts[0])
        amt = Decimal(parts[1])
    except (ValueError, InvalidOperation):
        await message.answer("Invalid arguments.")
        return
    note = parts[2] if len(parts) > 2 else "admin credit"
    await wallet.credit(session, user_id=uid, amount=amt, kind="admin_credit", note=note)
    await message.answer(f"Credited {amt:.2f} USDT to {uid}.")


@router.message(Command("debit"))
async def debit_user(message: Message, command: CommandObject, session: AsyncSession) -> None:
    if not is_admin(message.from_user.id):
        return
    parts = (command.args or "").split(maxsplit=2)
    if len(parts) < 2:
        await message.answer("Usage: /debit USER_ID 5.00 [note]")
        return
    try:
        uid = int(parts[0])
        amt = Decimal(parts[1])
    except (ValueError, InvalidOperation):
        await message.answer("Invalid arguments.")
        return
    note = parts[2] if len(parts) > 2 else "admin debit"
    try:
        await wallet.debit(session, user_id=uid, amount=amt, kind="admin_debit", note=note)
    except ValueError as e:
        await message.answer(str(e))
        return
    await message.answer(f"Debited {amt:.2f} USDT from {uid}.")


@router.message(Command("ban"))
async def ban(message: Message, command: CommandObject, session: AsyncSession) -> None:
    if not is_admin(message.from_user.id):
        return
    try:
        uid = int((command.args or "").strip())
    except ValueError:
        await message.answer("Usage: /ban USER_ID")
        return
    u = await get_user(session, uid)
    if u is None:
        await message.answer("Not found.")
        return
    u.is_banned = True
    await session.commit()
    await message.answer(f"Banned {uid}.")


@router.message(Command("unban"))
async def unban(message: Message, command: CommandObject, session: AsyncSession) -> None:
    if not is_admin(message.from_user.id):
        return
    try:
        uid = int((command.args or "").strip())
    except ValueError:
        await message.answer("Usage: /unban USER_ID")
        return
    u = await get_user(session, uid)
    if u is None:
        await message.answer("Not found.")
        return
    u.is_banned = False
    await session.commit()
    await message.answer(f"Unbanned {uid}.")


# ─── Broadcast ────────────────────────────────────────────────────────────────

@router.message(Command("payments"))
async def payments_audit(message: Message, command: CommandObject, session: AsyncSession) -> None:
    if not is_admin(message.from_user.id):
        return
    status = ((command.args or "").strip().lower() or "all")
    rows = await payments_repo.list_payment_verifications(session, status=status, limit=40)
    if not rows:
        await message.answer("No payment verification records found.")
        return
    lines = ["<b>Payment verifications</b>"]
    for p in rows:
        lines.append(
            f"#{p.id} · user <code>{p.user_id}</code> · product <code>{p.product_id}</code> · "
            f"qty {p.qty} · expected {p.expected_amount_usdt:.2f} · "
            f"status <b>{p.status}</b> · attempts {p.attempts}\n"
            f"ref: <code>{p.reference}</code>"
        )
    await message.answer("\n\n".join(lines), parse_mode="HTML")


@router.message(Command("approve_pay"))
async def approve_pay(message: Message, command: CommandObject, session: AsyncSession) -> None:
    if not is_admin(message.from_user.id):
        return
    args = (command.args or "").split(maxsplit=1)
    if not args:
        await message.answer("Usage: /approve_pay ID [note]")
        return
    try:
        pay_id = int(args[0])
    except ValueError:
        await message.answer("Bad ID.")
        return
    note = args[1] if len(args) > 1 else "manual admin approval"
    _ok, msg = await _approve_payment(
        session,
        bot=message.bot,
        admin_id=message.from_user.id,
        pay_id=pay_id,
        note=note,
    )
    await message.answer(msg)


@router.message(Command("reject_pay"))
async def reject_pay(message: Message, command: CommandObject, session: AsyncSession) -> None:
    if not is_admin(message.from_user.id):
        return
    args = (command.args or "").split(maxsplit=1)
    if not args:
        await message.answer("Usage: /reject_pay ID reason")
        return
    try:
        pay_id = int(args[0])
    except ValueError:
        await message.answer("Bad ID.")
        return
    reason = args[1] if len(args) > 1 else "rejected by admin"

    payment = await payments_repo.get_payment_verification(session, pay_id)
    if payment is None:
        await message.answer("Payment record not found.")
        return
    if payment.status == "delivered":
        await message.answer("Cannot reject: this payment is already delivered.")
        return

    await payments_repo.mark_rejected(
        session,
        payment,
        status="manual_rejected",
        note=reason,
        decided_by=message.from_user.id,
    )
    await products_repo.release_user_reservations(
        session,
        product_id=payment.product_id,
        user_id=payment.user_id,
    )
    with contextlib.suppress(Exception):
        await message.bot.send_message(
            payment.user_id,
            texts.payment_rejected(payment.reference) + f"\n\nReason: {reason}",
            parse_mode="HTML",
        )
    await message.answer(f"Payment #{pay_id} rejected.")


@router.callback_query(F.data.startswith(f"{kb.CB_ADMIN_PAY_APPROVE}:"))
async def approve_pay_button(cb: CallbackQuery, session: AsyncSession) -> None:
    if not is_admin(cb.from_user.id):
        await cb.answer()
        return
    pay_id = int(cb.data.split(":")[2])
    ok, msg = await _approve_payment(
        session,
        bot=cb.bot,
        admin_id=cb.from_user.id,
        pay_id=pay_id,
        note="manual admin approval",
    )
    await cb.answer(msg, show_alert=not ok)
    if cb.message is not None:
        with contextlib.suppress(Exception):
            await cb.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith(f"{kb.CB_ADMIN_PAY_REJECT}:"))
async def reject_pay_button(cb: CallbackQuery, session: AsyncSession) -> None:
    if not is_admin(cb.from_user.id):
        await cb.answer()
        return
    pay_id = int(cb.data.split(":")[2])
    payment = await payments_repo.get_payment_verification(session, pay_id)
    if payment is None:
        await cb.answer("Payment record not found.", show_alert=True)
        return
    if payment.status == "delivered":
        await cb.answer("Cannot reject: already delivered.", show_alert=True)
        return
    await payments_repo.mark_rejected(
        session,
        payment,
        status="manual_rejected",
        note="manual admin rejection",
        decided_by=cb.from_user.id,
    )
    await products_repo.release_user_reservations(
        session,
        product_id=payment.product_id,
        user_id=payment.user_id,
    )
    with contextlib.suppress(Exception):
        await cb.bot.send_message(
            payment.user_id,
            texts.payment_rejected(payment.reference),
            parse_mode="HTML",
        )
    await cb.answer(f"Payment #{pay_id} rejected.")
    if cb.message is not None:
        with contextlib.suppress(Exception):
            await cb.message.edit_reply_markup(reply_markup=None)


async def _approve_payment(
    session: AsyncSession,
    *,
    bot,
    admin_id: int,
    pay_id: int,
    note: str,
) -> tuple[bool, str]:
    payment = await payments_repo.get_payment_verification(session, pay_id)
    if payment is None:
        return False, "Payment record not found."
    if payment.status == "delivered":
        return False, "Already delivered."

    await payments_repo.mark_manual_approved(
        session,
        payment,
        admin_id=admin_id,
        note=note,
        received_amount=payment.received_amount_usdt or payment.expected_amount_usdt,
    )

    product = await session.get(Product, payment.product_id)
    if product is None:
        await payments_repo.mark_rejected(
            session,
            payment,
            status="delivery_failed",
            note="product_missing",
            decided_by=admin_id,
        )
        return False, "Approved but delivery failed: product no longer exists."

    try:
        result = await buy_product_quantity(
            session,
            user_id=payment.user_id,
            product_id=payment.product_id,
            qty=payment.qty,
        )
    except OutOfStock:
        await payments_repo.mark_rejected(
            session,
            payment,
            status="delivery_failed",
            note="out_of_stock_manual_approval",
            decided_by=admin_id,
        )
        await products_repo.release_user_reservations(
            session,
            product_id=payment.product_id,
            user_id=payment.user_id,
        )
        return False, "Approved but delivery failed: out of stock."
    except BuyError as e:
        await payments_repo.mark_rejected(
            session,
            payment,
            status="delivery_failed",
            note=str(e),
            decided_by=admin_id,
        )
        await products_repo.release_user_reservations(
            session,
            product_id=payment.product_id,
            user_id=payment.user_id,
        )
        return False, f"Approved but delivery failed: {e}"

    order_ids = [o.id for o in result.orders]
    await payments_repo.mark_delivered(session, payment, order_ids=order_ids)

    with contextlib.suppress(Exception):
        await bot.send_message(
            payment.user_id,
            texts.payment_verified_detail(
                amount=payment.received_amount_usdt or payment.expected_amount_usdt,
                reference=payment.reference,
                network="USDT BEP20" if payment.provider == "bep20" else "USDT Binance Pay",
            ),
            parse_mode="HTML",
        )
    with contextlib.suppress(Exception):
        await bot.send_message(
            payment.user_id,
            texts.order_success(
                name=result.product.display_name,
                duration=result.product.duration_label,
                qty=payment.qty,
                total=payment.expected_amount_usdt,
                reference=payment.reference,
                payloads=result.payloads,
            ),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    return True, f"Manual approval complete. Delivered {len(order_ids)} item(s)."

@router.message(Command("broadcast"))
async def broadcast_cmd(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.set_state(AdminStates.waiting_broadcast)
    await message.answer("Send the broadcast message now (text or photo with caption). It will be forwarded to every active user. Send /cancel to abort.")


@router.message(Command("cancel"), AdminStates.waiting_broadcast)
async def broadcast_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Broadcast cancelled.")


@router.message(AdminStates.waiting_broadcast)
async def broadcast_send(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    users = (await session.scalars(select(User).where(User.is_banned.is_(False)))).all()
    sent = 0
    failed = 0
    for u in users:
        if not u.notifications_enabled:
            continue
        try:
            await message.copy_to(chat_id=u.id)
            sent += 1
        except Exception:
            failed += 1
    await message.answer(f"Broadcast complete. Sent: {sent}. Failed: {failed}.")


# ─── Premium emoji helpers ────────────────────────────────────────────────────

@router.message(Command("getemoji"))
async def get_emoji(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    target = message.reply_to_message or message
    entities = (target.entities or []) + (target.caption_entities or [])
    found = []
    for e in entities:
        if e.type == "custom_emoji" and e.custom_emoji_id:
            found.append(e.custom_emoji_id)
    if not found:
        await message.answer(
            "Reply to a message that contains <b>premium custom emojis</b> with /getemoji to capture their IDs.",
            parse_mode="HTML",
        )
        return
    lines = "\n".join(f"<code>{eid}</code>" for eid in found)
    await message.answer(
        f"Found custom_emoji_id(s):\n{lines}\n\n"
        "Add them to <code>assets/premium_emojis.json</code> and run /reload_emojis.",
        parse_mode="HTML",
    )


@router.message(Command("reload_emojis"))
async def reload_emojis(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    reload_map()
    await message.answer("Premium emoji map reloaded.")


@router.message(Command("show_emojis"))
async def show_emojis(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    keys = list_loaded_keys()
    if not keys:
        await message.answer("No emoji entries loaded.")
        return
    lines = [f"{len(keys)} keys loaded:"]
    # show a compact sample first
    sample = ", ".join(keys[:50])
    lines.append(sample)
    if len(keys) > 50:
        lines.append(f"... and {len(keys) - 50} more keys")
    warnings = validate_registry()
    if warnings:
        lines.append("\nWarnings:")
        lines.extend(warnings[:20])
        if len(warnings) > 20:
            lines.append(f"... and {len(warnings) - 20} more warnings")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("stats"))
async def stats(message: Message, session: AsyncSession) -> None:
    if not is_admin(message.from_user.id):
        return
    n_users = int(await session.scalar(select(func.count(User.id))) or 0)
    n_orders = int(await session.scalar(select(func.count(Order.id))) or 0)
    n_wd_pending = int(await session.scalar(
        select(func.count(Withdrawal.id)).where(Withdrawal.status == "pending")
    ) or 0)
    n_stock = int(await session.scalar(
        select(func.count(StockItem.id)).where(StockItem.status == "available")
    ) or 0)
    order_value = Decimal(str(await session.scalar(
        select(func.coalesce(func.sum(Order.price_usdt), 0))
    ) or 0))
    last_order = await session.scalar(select(Order).order_by(desc(Order.id)).limit(1))
    await message.answer(
        f"<b>Bot stats</b>\n\n"
        f"Users: {n_users}\n"
        f"Orders: {n_orders}\n"
        f"Order value: {order_value:.2f} USDT\n"
        f"Available stock: {n_stock}\n"
        f"Pending withdrawals: {n_wd_pending}\n"
        f"Last order id: {last_order.id if last_order else '-'}",
        parse_mode="HTML",
    )
