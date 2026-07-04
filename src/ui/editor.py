"""Single-message edit utility.

Every menu navigation should call ``safe_edit`` instead of sending a new
message, so the bot keeps a single, scrolling-free conversation. If the
original message can't be edited (deleted by user, too old, message not
modified, etc.) we fall back to sending a new one and update the user's
``last_menu_message_id`` so subsequent edits target the new message.
"""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from ..repositories.users import set_last_menu_message

log = logging.getLogger(__name__)


async def render(
    *,
    bot: Bot,
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    text: str,
    keyboard: InlineKeyboardMarkup | None,
    message_id: int | None = None,
) -> int:
    """Edit message_id if possible, otherwise send a new message.

    Returns the message_id that the menu now lives on.
    """
    if message_id is not None:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            await set_last_menu_message(session, user_id, chat_id, message_id)
            return message_id
        except TelegramBadRequest as e:
            err = str(e).lower()
            if "message is not modified" in err:
                # Nothing to do — keyboard/text identical.
                return message_id
            if "message to edit not found" in err or "message can't be edited" in err:
                log.info("edit_message_text failed (%s); falling back to send", e)
            else:
                log.warning("edit_message_text TelegramBadRequest: %s", e)

    msg: Message = await bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=keyboard,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await set_last_menu_message(session, user_id, chat_id, msg.message_id)
    return msg.message_id


async def render_from_callback(
    cb: CallbackQuery,
    *,
    session: AsyncSession,
    text: str,
    keyboard: InlineKeyboardMarkup | None,
) -> int:
    """Convenience wrapper for callback handlers."""
    assert cb.message is not None
    return await render(
        bot=cb.bot,  # type: ignore[arg-type]
        session=session,
        user_id=cb.from_user.id,
        chat_id=cb.message.chat.id,
        text=text,
        keyboard=keyboard,
        message_id=cb.message.message_id,
    )
