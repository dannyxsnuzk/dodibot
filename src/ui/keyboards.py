"""All inline keyboards live here so menus stay consistent.

Bot API 9.4 added two relevant fields to ``InlineKeyboardButton``:

* ``icon_custom_emoji_id`` \u2014 a premium custom emoji rendered to the left of
  the label by Telegram itself (as opposed to a plain unicode glyph baked
  into the text).
* ``style`` \u2014 ``"primary"`` (blue), ``"success"`` (green) or ``"danger"``
  (red). Omitted = client default.

The :func:`btn` helper applies both based on a logical emoji name and the
button's semantic role so the colour scheme stays symmetric across the bot:

* navigation / informational buttons \u2192 ``primary`` (blue)
* positive commits (Buy, Confirm, Submit, Generate, Transfer, Enabled-toggle)
  \u2192 ``success`` (green)
* destructive / cancel actions (Cancel, Revoke, Out of stock,
  Disabled-toggle) \u2192 ``danger`` (red)

Both features can be turned off via the ``PREMIUM_BUTTON_ICONS`` and
``BUTTON_STYLES_ENABLED`` env vars (see :mod:`src.config`).
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..config import get_settings
from .emoji import emoji_id, fb

# Callback data tokens. Keep short \u2014 Telegram caps callback_data at 64 bytes.
CB_MAIN = "main"
CB_SHOP = "shop"
CB_MY_ORDERS = "my_orders"
CB_PROFILE = "profile"
CB_SUPPORT = "support"
CB_REFER = "refer"
CB_DEPOSIT = "deposit"
CB_DEPOSIT_BINANCE = "dep:binance"
CB_DEPOSIT_BEP20 = "dep:bep20"
CB_DEPOSIT_VERIFY = "dep:verify"
CB_DEPOSIT_VERIFY_ORDER = "dep:v:order"
CB_DEPOSIT_VERIFY_UID = "dep:v:uid"

CB_REFRESH_SHOP = "shop:refresh"
CB_PRODUCT = "shop:p"           # shop:p:<id>
CB_BUY = "shop:buy"             # shop:buy:<product_id>
CB_BUY_CONFIRM = "shop:bc"      # shop:bc:<product_id>
CB_QTY = "shop:q"               # shop:q:<product_id>:<qty>
CB_QTY_CUSTOM = "shop:qc"       # shop:qc:<product_id>
CB_ORDER_SUMMARY = "shop:os"    # shop:os:<product_id>:<qty>
CB_PAY_DIRECT = "shop:pd"       # shop:pd:<product_id>:<qty>
CB_PAY_METHODS = "shop:pm"      # shop:pm:<product_id>:<qty>
CB_PAY_BINANCE = "shop:pb"      # shop:pb:<product_id>:<qty>
CB_PAY_BEP20 = "shop:pc"        # shop:pc:<product_id>:<qty>
CB_CANCEL_ORDER = "shop:cx"     # shop:cx:<product_id>
CB_MANUAL_REVIEW = "shop:mr"    # shop:mr:<payment_id>
CB_ADMIN_PAY_APPROVE = "adm:pa" # adm:pa:<payment_id>
CB_ADMIN_PAY_REJECT = "adm:pr"  # adm:pr:<payment_id>


CB_PROFILE_STATS = "prof:stats"
CB_PROFILE_NOTIFS = "prof:notifs"
CB_PROFILE_NOTIFS_TOGGLE = "prof:notifs:t"
CB_PROFILE_ORDERS = "prof:orders"        # prof:orders:<page>
CB_PROFILE_ORDER = "prof:order"          # prof:order:<id>
CB_PROFILE_WITHDRAW = "prof:wd"
CB_PROFILE_WITHDRAW_METHOD = "prof:wd:m"  # prof:wd:m:<binance|upi>
CB_PROFILE_API = "prof:api"
CB_PROFILE_API_NEW = "prof:api:new"
CB_PROFILE_API_REVOKE = "prof:api:rev"   # prof:api:rev:<id>

CB_REFER_COPY = "ref:copy"
CB_REFER_TRANSFER = "ref:txfer"

CB_NOOP = "noop"


# Telegram only accepts these three string values for the ``style`` field.
ButtonStyle = Literal["primary", "success", "danger"]


def _row(*buttons: InlineKeyboardButton) -> list[InlineKeyboardButton]:
    return list(buttons)


def _coerce_custom_emoji_id(value: str | int | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, int):
        return str(value)
    value = str(value).strip()
    if not value.isdigit():
        return None
    return value


def btn(
    label: str,
    *,
    icon: str | None = None,
    style: ButtonStyle | None = "primary",
    callback_data: str | None = None,
    url: str | None = None,
    **extra: object,
) -> InlineKeyboardButton:
    """Build an ``InlineKeyboardButton`` with premium-emoji icon + colour.

    ``icon`` is a logical emoji name (key in ``assets/premium_emojis.json``).
    When premium icons are enabled and the registry has an ID for that name,
    the icon is rendered via the ``icon_custom_emoji_id`` field. Otherwise
    the unicode fallback is prepended to the label so older clients and
    non-Premium owners still get a glyph.

    ``style`` defaults to ``"primary"`` so menu/navigation buttons share a
    consistent blue palette. Use ``"success"`` for positive commits and
    ``"danger"`` for destructive/cancel actions. Pass ``style=None`` to opt
    out and use the client's default colour.
    """
    settings = get_settings()
    text = label
    icon_id: str | None = None
    if icon:
        glyph = fb(icon)
        eid = emoji_id(icon) if settings.premium_button_icons else None
        icon_id = _coerce_custom_emoji_id(eid)
        if glyph:
            text = f"{glyph} {label}" if label else glyph

    fields: dict[str, object] = {"text": text}
    if icon_id is not None:
        fields["icon_custom_emoji_id"] = icon_id
    if style is not None and settings.button_styles_enabled:
        fields["style"] = style
    if callback_data is not None:
        fields["callback_data"] = callback_data
    if url is not None:
        fields["url"] = url
    fields.update(extra)
    return InlineKeyboardButton(**fields)  # type: ignore[arg-type]


def back_button(target: str = CB_MAIN, label: str | None = None) -> InlineKeyboardButton:
    return btn(label or "Back", icon="back", style="primary", callback_data=target)


def home_button() -> InlineKeyboardButton:
    return btn("Main Menu", icon="home", style="primary", callback_data=CB_MAIN)


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            _row(
                btn("🛒 Shop", style="primary", callback_data=CB_SHOP),
                btn("💰 Deposit", style="success", callback_data=CB_DEPOSIT),
            ),
            _row(
                btn("👤 My Profile", style="success", callback_data=CB_PROFILE),
                btn("📦 My Orders", style="success", callback_data=CB_MY_ORDERS),
            ),
            _row(
                btn("🆘 Support", style="success", callback_data=CB_SUPPORT),
                btn("⭐ Refer & Earn", style="success", callback_data=CB_REFER),
            ),
        ]
    )


def deposit_methods_kb(
    *,
    binance_enabled: bool,
    bep20_enabled: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if binance_enabled:
        rows.append(_row(btn(
            "🟡 Binance Pay (UID / Order ID)",
            style="primary",
            callback_data=CB_DEPOSIT_BINANCE,
        )))
    if bep20_enabled:
        rows.append(_row(btn(
            "🟢 BEP20 (USDT)", style="success", callback_data=CB_DEPOSIT_BEP20
        )))
    rows.append(_row(btn("🔙 Back", style="success", callback_data=CB_MAIN)))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def binance_verification_kb(
    *,
    order_id_enabled: bool,
    uid_enabled: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if order_id_enabled:
        rows.append(_row(btn(
            "🟡 Order ID", style="primary", callback_data=CB_DEPOSIT_VERIFY_ORDER
        )))
    if uid_enabled:
        rows.append(_row(btn(
            "🟢 UID Payment", style="success", callback_data=CB_DEPOSIT_VERIFY_UID
        )))
    rows.append(_row(btn(
        "🔙 Back", style="primary", callback_data=CB_DEPOSIT_BINANCE
    )))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def deposit_cancel_kb(target: str = CB_DEPOSIT) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[_row(btn(
            "🔙 Back", style="success", callback_data=target
        ))]
    )


def shop_list_kb(
    products: Iterable[tuple[int, str, str, str | None, str, int]],
) -> InlineKeyboardMarkup:
    """Build the shop product list.

    ``products`` yields ``(id, display_name, emoji, emoji_id, duration, stock)``
    tuples \u2014 ``emoji_id`` is the per-product premium custom emoji id from
    the ``products.emoji_id`` column (may be ``None``).
    """
    settings = get_settings()
    rows: list[list[InlineKeyboardButton]] = []
    for pid, name, emoji, eid, duration, stock in products:
        label = f"{name} {duration} ({stock})"
        fields: dict[str, object] = {
            "text": label,
            "callback_data": f"{CB_PRODUCT}:{pid}",
        }
        icon_id = _coerce_custom_emoji_id(eid) if settings.premium_button_icons else None
        if icon_id is not None:
            fields["icon_custom_emoji_id"] = icon_id
        else:
            fields["text"] = f"{emoji} {label}" if emoji else label
        if settings.button_styles_enabled:
            fields["style"] = "primary"
        rows.append(_row(InlineKeyboardButton(**fields)))  # type: ignore[arg-type]
    rows.append(_row(btn("🔄 Refresh", style="success", callback_data=CB_REFRESH_SHOP)))
    rows.append(_row(btn("◀️ Back", style="success", callback_data=CB_MAIN)))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def product_detail_kb(product_id: int, can_buy: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if can_buy:
        rows.append(_row(
            btn("🛒 Buy Now", style="primary",
                callback_data=f"{CB_BUY}:{product_id}")
        ))
    else:
        rows.append(_row(
            btn("Out of stock", icon="cross", style="danger", callback_data=CB_NOOP)
        ))
    rows.append(_row(btn("◀️ Back to Store", style="success", callback_data=CB_SHOP)))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def buy_confirm_kb(product_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            _row(
                btn("Confirm", icon="check", style="success",
                    callback_data=f"{CB_BUY_CONFIRM}:{product_id}"),
                btn("Cancel", icon="cross", style="danger",
                    callback_data=f"{CB_PRODUCT}:{product_id}"),
            ),
        ]
    )


def quantity_kb(product_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            _row(
                btn("1", style="primary", callback_data=f"{CB_QTY}:{product_id}:1"),
                btn("2", style="primary", callback_data=f"{CB_QTY}:{product_id}:2"),
                btn("3", style="primary", callback_data=f"{CB_QTY}:{product_id}:3"),
            ),
            _row(btn(
                "Custom Amount", icon="edit", style="success",
                callback_data=f"{CB_QTY_CUSTOM}:{product_id}",
            )),
            _row(
                btn("Back", icon="back", style="success", callback_data=f"{CB_PRODUCT}:{product_id}"),
                home_button(),
            ),
        ]
    )


def custom_quantity_kb(product_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            _row(
                btn("Back", icon="back", style="success", callback_data=f"{CB_BUY}:{product_id}"),
                btn(
                    "Cancel Order", icon="cross", style="danger",
                    callback_data=f"{CB_CANCEL_ORDER}:{product_id}",
                ),
            ),
        ]
    )


def order_summary_kb(product_id: int, qty: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            _row(btn(
                "Pay directly", icon="coin", style="success",
                callback_data=f"{CB_PAY_METHODS}:{product_id}:{qty}",
            )),
            _row(
                btn("Back", icon="back", style="success", callback_data=f"{CB_BUY}:{product_id}"),
                btn(
                    "Cancel Order", icon="cross", style="danger",
                    callback_data=f"{CB_CANCEL_ORDER}:{product_id}",
                ),
            ),
        ]
    )


def payment_methods_kb(product_id: int, qty: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            _row(btn(
                "Binance Pay", icon="binance", style="success",
                callback_data=f"{CB_PAY_BINANCE}:{product_id}:{qty}",
            )),
            _row(btn(
                "USDT BEP20", icon="coin", style="success",
                callback_data=f"{CB_PAY_BEP20}:{product_id}:{qty}",
            )),
            _row(
                btn("Back", icon="back", style="success", callback_data=f"{CB_ORDER_SUMMARY}:{product_id}:{qty}"),
                btn(
                    "Cancel Order", icon="cross", style="danger",
                    callback_data=f"{CB_CANCEL_ORDER}:{product_id}",
                ),
            ),
        ]
    )


def binance_payment_kb(product_id: int, qty: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            _row(
                btn("Back", icon="back", style="success", callback_data=f"{CB_PAY_METHODS}:{product_id}:{qty}"),
                btn(
                    "Cancel Order", icon="cross", style="danger",
                    callback_data=f"{CB_CANCEL_ORDER}:{product_id}",
                ),
            ),
        ]
    )


def bep20_payment_kb(product_id: int, qty: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            _row(
                btn("Back", icon="back", style="success", callback_data=f"{CB_PAY_METHODS}:{product_id}:{qty}"),
                btn(
                    "Cancel Order", icon="cross", style="danger",
                    callback_data=f"{CB_CANCEL_ORDER}:{product_id}",
                ),
            ),
        ]
    )


def manual_review_kb(payment_id: int) -> InlineKeyboardMarkup:
    support_username = get_settings().support_username.strip().lstrip("@")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            _row(btn(
                "Submit for Manual Review", icon="support", style="success",
                callback_data=f"{CB_MANUAL_REVIEW}:{payment_id}",
            )),
            _row(btn(
                "Contact Support", icon="support", style="primary",
                url=f"https://t.me/{support_username}",
            )),
            _row(home_button()),
        ]
    )


def admin_payment_review_kb(payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            _row(
                btn(
                    "Approve", icon="check", style="success",
                    callback_data=f"{CB_ADMIN_PAY_APPROVE}:{payment_id}",
                ),
                btn(
                    "Reject", icon="cross", style="danger",
                    callback_data=f"{CB_ADMIN_PAY_REJECT}:{payment_id}",
                ),
            ),
        ]
    )


def profile_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            _row(
                btn("My Stats", icon="stats", style="primary", callback_data=CB_PROFILE_STATS),
                btn("Notifications", icon="bell", style="primary", callback_data=CB_PROFILE_NOTIFS),
            ),
            _row(btn("My Orders", icon="orders", style="primary", callback_data=f"{CB_PROFILE_ORDERS}:1")),
            _row(btn("Back", icon="back", style="success", callback_data=CB_MAIN)),
        ]
    )


def notifs_kb(enabled: bool) -> InlineKeyboardMarkup:
    if enabled:
        toggle = btn(
            "Enabled \u2014 tap to disable",
            icon="check", style="success",
            callback_data=CB_PROFILE_NOTIFS_TOGGLE,
        )
    else:
        toggle = btn(
            "Disabled \u2014 tap to enable",
            icon="cross", style="danger",
            callback_data=CB_PROFILE_NOTIFS_TOGGLE,
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            _row(toggle),
            _row(btn("Back", icon="back", style="success", callback_data=CB_PROFILE)),
        ]
    )


def orders_kb(orders: list[tuple[int, str]], page: int, has_next: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for oid, label in orders:
        rows.append(_row(btn(
            label, style="primary",
            callback_data=f"{CB_PROFILE_ORDER}:{oid}",
        )))
    nav: list[InlineKeyboardButton] = []
    if page > 1:
        nav.append(btn("\u25c0 Prev", style="primary",
                       callback_data=f"{CB_PROFILE_ORDERS}:{page-1}"))
    if has_next:
        nav.append(btn("Next \u25b6", style="primary",
                       callback_data=f"{CB_PROFILE_ORDERS}:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append(_row(btn("Back", icon="back", style="success", callback_data=CB_PROFILE)))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def order_detail_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            _row(
                btn("My Orders", icon="orders", style="primary", callback_data=f"{CB_PROFILE_ORDERS}:1"),
                home_button(),
            ),
        ]
    )


def withdraw_method_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            _row(
                btn("Binance UID", icon="binance", style="primary",
                    callback_data=f"{CB_PROFILE_WITHDRAW_METHOD}:binance"),
                btn("UPI", icon="upi", style="primary",
                    callback_data=f"{CB_PROFILE_WITHDRAW_METHOD}:upi"),
            ),
            _row(btn("Back", icon="back", style="success", callback_data=CB_PROFILE)),
        ]
    )


def withdraw_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[_row(btn(
            "Cancel", icon="cross", style="danger", callback_data=CB_PROFILE,
        ))]
    )


def api_kb(has_token: bool, token_id: int | None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if has_token and token_id is not None:
        rows.append(_row(btn(
            "Revoke token", icon="cross", style="danger",
            callback_data=f"{CB_PROFILE_API_REVOKE}:{token_id}",
        )))
    rows.append(_row(btn(
        "Generate new token", icon="rocket", style="success",
        callback_data=CB_PROFILE_API_NEW,
    )))
    rows.append(_row(btn("Back", icon="back", style="success", callback_data=CB_PROFILE)))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def support_kb(support_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            _row(btn("Contact Support", icon="support",
                     style="primary", url=f"https://t.me/{support_username}")),
            _row(btn("Back", icon="back", style="success", callback_data=CB_MAIN)),
        ]
    )


def refer_kb(referral_link: str, has_balance: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    # ``copy_text`` was added to Bot API 7.10 \u2014 when the user taps it
    # Telegram copies the link to their clipboard with no extra dialog.
    try:
        from aiogram.types import CopyTextButton
        copy_btn = btn(
            "Copy Referral Link", icon="clipboard",
            style="primary",
            copy_text=CopyTextButton(text=referral_link),
        )
    except Exception:
        # Older aiogram or older Telegram client \u2014 fall back to a callback
        # that shows the link in an alert so it can be copied manually.
        copy_btn = btn(
            "Copy Referral Link", icon="clipboard",
            style="primary",
            callback_data=CB_REFER_COPY,
        )
    rows.append(_row(copy_btn))
    rows.append(_row(btn(
        "Share with a friend", icon="link", style="primary",
        url=f"https://t.me/share/url?url={referral_link}",
    )))
    if has_balance:
        rows.append(_row(btn(
            "Transfer to Wallet", icon="transfer", style="success",
            callback_data=CB_REFER_TRANSFER,
        )))
    rows.append(_row(btn("Back", icon="back", style="success", callback_data=CB_MAIN)))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cancel_to_main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[_row(btn(
            "Cancel", icon="cross", style="danger", callback_data=CB_MAIN,
        ))]
    )
