"""Message body builders. All in HTML mode so we can mix premium emojis."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from html import escape

from ..config import get_settings
from .emoji import pe, pe_custom


def _h(s: str) -> str:
    return escape(s, quote=False)


def main_menu(user_first_name: str) -> str:
    s = get_settings()
    name = _h(user_first_name or "friend")
    return (
        f"{pe('sparkle')} <b>Welcome to {_h(s.shop_name)}!</b>\n\n"
        f"Hey, <b>{name}</b> {pe('wave')}\n\n"
        "We offer premium digital products at the best prices. Fast, secure, "
        "and fully automated delivery.\n\n"
        f"<blockquote>"
        f"{pe('shop')} <b>Shop</b> — Browse &amp; buy products\n"
        f"{pe('user')} <b>My Profile</b> — Balance, orders &amp; settings\n"
        f"{pe('support')} <b>Support</b> — Get help\n"
        f"{pe('refer')} <b>Refer &amp; Earn</b> — Invite friends &amp; earn rewards"
        f"</blockquote>\n\n"
        f"Choose an option below to continue! {pe('point_down')}"
    )

def shop_header(total: int, reserved_product: str | None = None) -> str:
    title = "<b>Choose Your Product:</b>"
    if reserved_product:
        title = f"<b>Choose Your Product:</b> {escape(reserved_product, quote=False)}"
    return f"{pe('cart')} {title}"


def shop_empty() -> str:
    return (
        f"{pe('cart')} <b>Shop</b>\n\n"
        f"{pe('warning')} No products available right now. Please check back soon."
    )


def product_detail(
    *, name: str, emoji: str, emoji_id: str | None, duration: str, price: Decimal,
    description: str, stock: int,
) -> str:
    desc = _h(description.strip()) if description.strip() else "<i>No description.</i>"
    stock_line = (
        f"{pe('check')} <b>In stock:</b> {stock}" if stock > 0
        else f"{pe('cross')} <b>Out of stock</b>"
    )
    rendered_emoji = pe_custom(emoji_id, emoji)
    return (
        f"{rendered_emoji} <b>{_h(name)} {_h(duration)}</b>\n\n"
        f"Price: <b>${price:.2f} / code</b>\n"
        f"{stock_line}\n\n"
        f"<blockquote>{desc}</blockquote>\n\n"
        f"<i>Delivery is automatic after payment confirmation.</i>"
    )


def buy_confirm(*, name: str, price: Decimal) -> str:
    return (
        f"{pe('cart')} <b>Confirm order</b>\n\n"
        f"Product: <b>{_h(name)}</b>\n"
        f"Price: <b>{price:.2f} USDT</b>\n"
        "\nPress <b>Confirm</b> to continue."
    )


def buy_quantity(
    *,
    name: str,
    emoji: str,
    emoji_id: str | None,
    duration: str,
    price: Decimal,
) -> str:
    rendered_emoji = pe_custom(emoji_id, emoji)
    return (
        "<b>Select Quantity</b>\n\n"
        f"{rendered_emoji} <b>{_h(name)} {_h(duration)}</b>\n"
        f"${price:.2f} / code\n\n"
        "How many codes do you want?"
    )


def order_summary(
    *,
    name: str,
    emoji: str,
    emoji_id: str | None,
    duration: str,
    qty: int,
    price_each: Decimal,
) -> str:
    rendered_emoji = pe_custom(emoji_id, emoji)
    total = (price_each * Decimal(qty)).quantize(Decimal("0.01"))
    return (
        f"{pe('info')} <b>Order Summary</b>\n\n"
        f"{rendered_emoji} <b>{_h(name)} {_h(duration)}</b>\n"
        f"{pe('id')} Qty: <b>{qty}</b>\n"
        f"{pe('coin')} Price: <b>${price_each:.2f} each</b>\n"
        f"{pe('coin')} Total: <b>${total:.2f} USDT</b>\n\n"
        f"{pe('coin')} Choose a payment method:"
    )


def payment_method(
    *,
    name: str,
    emoji: str,
    emoji_id: str | None,
    duration: str,
    qty: int,
    price_each: Decimal,
) -> str:
    rendered_emoji = pe_custom(emoji_id, emoji)
    total = (price_each * Decimal(qty)).quantize(Decimal("0.01"))
    return (
        f"{pe('coin')} <b>Select Payment Method</b>\n\n"
        f"{rendered_emoji} <b>{_h(name)} {_h(duration)}</b> x {qty}\n"
        f"Total: <b>${total:.2f} USDT</b>"
    )


def binance_pay_instructions(
    *,
    name: str,
    duration: str,
    qty: int,
    price_each: Decimal,
    binance_id: str,
) -> str:
    return (
        "💡 You can send any amount — it will be added to your balance.\n"
        "___________________\n"
        "🏦 <b>Binance Pay / Internal Transfer</b>\n\n"
        "Binance ID:\n"
        f"<code>{_h(binance_id)}</code>\n"
        "👆 Tap to copy\n"
        "___________________\n"
        "After sending, paste your Transaction Hash (TxID) or Order ID here and we'll verify it automatically."
    )


def bep20_payment_instructions(
    *,
    name: str,
    duration: str,
    qty: int,
    price_each: Decimal,
    wallet_address: str,
) -> str:
    return (
        "💡 You can send any amount — it will be added to your balance.\n"
        "___________________\n"
        "🪙 <b>USDT (BEP20 - BSC)</b>\n\n"
        "Wallet Address:\n"
        f"<code>{_h(wallet_address)}</code>\n"
        "👆 Tap to copy\n"
        "___________________\n"
        "After sending, paste your Transaction Hash (TxID) or Order ID here and we'll verify it automatically."
    )


def custom_qty_prompt(*, name: str, price: Decimal) -> str:
    return (
        "<b>Custom Amount</b>\n\n"
        f"📦 {_h(name)}\n"
        f"${price:.2f} / code\n\n"
        "Send how many codes you want as a number (for example: <code>5</code>).\n"
        "Send <code>cancel</code> to cancel this order."
    )


def payment_verifying(*, reference: str, progress_pct: int, remaining_text: str) -> str:
    pct = max(0, min(100, progress_pct))
    filled = min(10, max(0, pct // 10))
    bar = "█" * filled + "░" * (10 - filled)
    return (
        "⏳ <b>Verifying Transaction...</b>\n\n"
        "<b>TxID:</b>\n"
        f"<code>{_h(reference)}</code>\n\n"
        f"<code>{bar}</code> <b>{pct}%</b>\n\n"
        "🔄 Checking blockchain &amp; exchange APIs...\n"
        f"⏱ {remaining_text} remaining\n\n"
        "Please wait while we confirm your payment."
    )


def payment_verified_binance(amount: Decimal) -> str:
    return (
        "✅ <b>Payment Verified! (Binance Pay)</b>\n\n"
        f"💰 Received: <b>{amount:.2f} USDT</b>\n\n"
        "⏳ Delivering your items..."
    )


def payment_verified_detail(
    *,
    amount: Decimal,
    reference: str,
    network: str = "USDT Binance Pay",
) -> str:
    return (
        "✅ <b>Payment Verified!</b>\n\n"
        f"💰 Amount: <b>{amount:.2f} USDT</b>\n"
        f"🪙 Network/Coin: <b>{_h(network)}</b>\n"
        f"🔗 TxID: <code>{_h(reference)}</code>\n\n"
        "⏳ Delivering your items..."
    )


def payment_rejected(reference: str, reason: str | None = None) -> str:
    reason_text = ""
    if reason:
        reason_text = f"\n\nReason: <b>{_h(reason)}</b>"
    return (
        "❌ <b>Deposit Rejected</b>\n\n"
        f"Your deposit (TxID: <code>{_h(reference)}</code>) could not be verified."
        f"{reason_text}\n\n"
        "If you believe this is an error, please contact support."
    )


def manual_review_submitted(*, payment_id: int, reference: str) -> str:
    return (
        "⏳ We couldn't auto-verify this yet. Your payment has been submitted for manual review and will be processed shortly."
    )


def admin_payment_review(
    *,
    payment_id: int,
    user_id: int,
    product: str,
    qty: int,
    expected: Decimal,
    reference: str,
) -> str:
    return (
        "🧾 <b>Manual Payment Review</b>\n\n"
        f"Payment: <code>#{payment_id}</code>\n"
        f"User: <code>{user_id}</code>\n"
        f"Product: <b>{_h(product)}</b>\n"
        f"Quantity: <b>{qty}</b>\n"
        f"Expected: <b>{expected:.2f} USDT</b>\n"
        f"TxID: <code>{_h(reference)}</code>\n\n"
        "Approve to deliver stock, or reject to notify the user."
    )


def payment_config_missing() -> str:
    return (
        "⚠️ <b>Verification Not Configured</b>\n\n"
        "Binance API key/secret are not set yet, so automatic verification is unavailable.\n\n"
        "Please contact support, or add BINANCE_API_KEY and BINANCE_SECRET_KEY in .env."
    )


def delivery_summary(*, name: str, duration: str, orders: list[tuple[int, str]]) -> str:
    parts: list[str] = [
        "🎉 <b>Order Delivered</b>",
        "",
        f"📦 <b>{_h(name)} {_h(duration)}</b>",
        f"🔢 Qty: <b>{len(orders)}</b>",
        "",
    ]
    for idx, (order_id, payload) in enumerate(orders, start=1):
        parts.append(f"<b>Code {idx}</b> · Order <code>#{order_id}</code>")
        parts.append(f"<pre>{_h(payload)}</pre>")
    parts.append("Saved in <b>My Profile → My Orders</b>.")
    return "\n".join(parts)


def order_success(
    *,
    name: str,
    duration: str,
    qty: int,
    total: Decimal,
    reference: str,
    payloads: list[str],
) -> str:
    parts = [
        "✅ <b>Order Successful!</b>",
        "",
        f"Product: <b>{_h(name)} {_h(duration)}</b>",
        f"Quantity: <b>{qty}</b>",
        f"Total: <b>{total:.2f} USDT</b>",
        f"🔗 TxID: <code>{_h(reference)}</code>",
        "",
        "",
        f"📦 <b>{_h(name)} {_h(duration)}</b> × {qty}",
        "",
    ]
    for idx, payload in enumerate(payloads, start=1):
        parts.append(f"{idx}. {_h(payload)}")
    return "\n".join(parts)


def buy_success(*, name: str, payload: str, order_id: int) -> str:
    return (
        f"{pe('check')} <b>Order complete!</b>\n\n"
        f"Product: <b>{_h(name)}</b>\n"
        f"Order #: <code>{order_id}</code>\n\n"
        f"{pe('lock')} <b>Your credentials:</b>\n"
        f"<pre>{_h(payload)}</pre>\n\n"
        "Save this info — you can also find it later under <b>My Profile → My Orders</b>."
    )


def profile(
    *, user_id: int, total_spent: Decimal, joined_at: datetime,
) -> str:
    return (
        f"{pe('user')} <b>User Profile</b>\n\n"
        f"{pe('id')} <b>ID:</b> <code>{user_id}</code>\n"
        f"{pe('coin')} <b>Total Spent:</b> {total_spent:.2f} USDT\n"
        f"{pe('calendar')} <b>Joined:</b> {joined_at.strftime('%Y-%m-%d')}"
    )


def profile_stats(
    *, total_orders: int, total_order_value: Decimal, referrals: int,
    referral_earned: Decimal,
) -> str:
    return (
        f"{pe('stats')} <b>My Stats</b>\n\n"
        f"Total orders: <b>{total_orders}</b>\n"
        f"Total order value: <b>{total_order_value:.2f} USDT</b>\n"
        f"Referrals: <b>{referrals}</b>\n"
        f"Referral earnings: <b>{referral_earned:.2f} USDT</b>"
    )


def profile_notifs(enabled: bool) -> str:
    state = "<b>ON</b>" if enabled else "<b>OFF</b>"
    return (
        f"{pe('bell')} <b>Notifications</b>\n\n"
        f"Status: {state}\n\n"
        "When ON, you'll receive order updates, "
        "withdrawal status, and admin announcements."
    )


def profile_orders_header(total: int, page: int, page_size: int) -> str:
    if total == 0:
        return (
            f"{pe('orders')} <b>My Orders</b>\n\n"
            "<i>No orders yet. Browse the shop to make your first order.</i>"
        )
    pages = max(1, (total + page_size - 1) // page_size)
    return (
        f"{pe('orders')} <b>My Orders</b>\n\n"
        f"Total: <b>{total}</b> · Page {page}/{pages}"
    )


def order_detail(*, order_id: int, name: str, price: Decimal, payload: str, created_at: datetime) -> str:
    return (
        f"{pe('orders')} <b>Order #{order_id}</b>\n\n"
        f"Product: <b>{_h(name)}</b>\n"
        f"Price: <b>{price:.2f} USDT</b>\n"
        f"Date: {created_at.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        f"{pe('lock')} <b>Credentials:</b>\n"
        f"<pre>{_h(payload)}</pre>"
    )


def withdraw_intro(*, balance: Decimal, min_amt: Decimal, max_amt: Decimal) -> str:
    return (
        f"{pe('withdraw')} <b>Withdraw</b>\n\n"
        f"Balance: <b>{balance:.2f} USDT</b>\n"
        f"Limits: {min_amt:.2f} – {max_amt:.2f} USDT\n\n"
        "Choose a payout method:"
    )


def withdraw_ask_amount(method: str, balance: Decimal) -> str:
    return (
        f"{pe('withdraw')} <b>Withdraw via {method.upper()}</b>\n\n"
        f"Balance: <b>{balance:.2f} USDT</b>\n\n"
        "Send the amount in <b>USDT</b> (e.g. <code>10</code>). "
        "Send <code>cancel</code> to abort."
    )


def withdraw_ask_address(method: str, amount: Decimal) -> str:
    addr_label = "Binance UID" if method == "binance" else "UPI ID"
    return (
        f"{pe('withdraw')} <b>Withdraw {amount:.2f} USDT via {method.upper()}</b>\n\n"
        f"Send your <b>{addr_label}</b> as the next message.\n"
        "Send <code>cancel</code> to abort."
    )


def withdraw_submitted(wid: int) -> str:
    return (
        f"{pe('check')} <b>Withdrawal request submitted</b>\n\n"
        f"Ticket #: <code>{wid}</code>\n\n"
        "An admin will process it within 24 hours."
    )


def api_screen(token: str | None) -> str:
    if not token:
        return (
            f"{pe('api')} <b>Developer API</b>\n\n"
            "<i>You don't have an API token yet.</i>\n\n"
            "Generate one to programmatically check stock and place orders. "
            "Endpoint docs: <code>/api/docs</code> on the admin dashboard."
        )
    return (
        f"{pe('api')} <b>Developer API</b>\n\n"
        f"Token: <code>{_h(token)}</code>\n\n"
        f"{pe('warning')} Keep this secret. Anyone with it can act as you."
    )


def api_token_created(token: str) -> str:
    return (
        f"{pe('check')} <b>New API token</b>\n\n"
        f"<code>{_h(token)}</code>\n\n"
        f"{pe('warning')} Copy it now — for security we'll only show it on this screen."
    )


def support_screen() -> str:
    s = get_settings()
    return (
        f"{pe('support')} <b>Need Help?</b>\n\n"
        "Contact our support team directly:\n"
        f"<b>@{_h(s.support_username)}</b>"
    )


def refer_screen(
    *, ref_24h: int, ref_7d: int, ref_total: int,
    earned_total: Decimal, available: Decimal, transferred: Decimal,
    referral_link: str,
) -> str:
    return (
        f"{pe('refer')} <b>Refer &amp; Earn</b>\n\n"
        f"{pe('people')} <b>Referred (24h):</b> {ref_24h}\n"
        f"{pe('people')} <b>Referred (7d):</b> {ref_7d}\n"
        f"{pe('people')} <b>Referred (Total):</b> {ref_total}\n\n"
        f"{pe('coin')} <b>Total Earned:</b> {earned_total:.2f} USDT\n"
        f"{pe('coin')} <b>Available:</b> {available:.2f} USDT\n"
        f"{pe('coin')} <b>Transferred:</b> {transferred:.2f} USDT\n\n"
        "<blockquote>Share your link to invite friends. Reward payouts can be wired "
        "into the future payment flow.</blockquote>\n\n"
        f"<b>Your referral link:</b>\n<code>{_h(referral_link)}</code>"
    )


def transfer_done(amount: Decimal) -> str:
    return (
        f"{pe('check')} <b>Transferred</b>\n\n"
        f"Moved <b>{amount:.2f} USDT</b> from referral earnings to your wallet."
    )


def transfer_nothing() -> str:
    return (
        f"{pe('info')} <b>Nothing to transfer</b>\n\n"
        "You have no available referral earnings right now."
    )


def banned() -> str:
    return f"{pe('cross')} <b>Your account is banned.</b> Contact support."


def help_text() -> str:
    return (
        f"{pe('info')} <b>How to use this bot</b>\n\n"
        f"{pe('shop')} <b>Shop</b> — pick a product, see the price and stock count, then tap "
        f"<b>Buy now</b>, choose quantity, pay, then submit the blockchain TxID for auto verification.\n\n"
        f"{pe('user')} <b>My Profile</b> — see your wallet balance, order history, "
        f"toggle notifications, withdraw funds, generate an API token.\n\n"
        f"{pe('refer')} <b>Refer &amp; Earn</b> — share your referral link to earn a "
        f"future rewards on eligible orders your friends make.\n\n"
        f"{pe('support')} <b>Support</b> — chat with our team if anything goes wrong.\n\n"
        "<blockquote>Tip: every button updates the same message instead of spamming "
        "new ones. Use the <b>Back</b> and <b>Main Menu</b> buttons to navigate.</blockquote>"
    )
