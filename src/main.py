"""Bot entrypoint: polling + DB init + seed + dashboard side-process."""
from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
)

from .config import get_settings
from .db.seed import seed_products
from .db.session import SessionLocal, init_db
from .handlers import admin as admin_handler
from .handlers import profile as profile_handler
from .handlers import refer as refer_handler
from .handlers import shop as shop_handler
from .handlers import start as start_handler
from .handlers import support as support_handler
from .middleware.banned import BannedUserMiddleware
from .middleware.db import DbSessionMiddleware
from .middleware.throttle import CallbackThrottleMiddleware
from .ui.emoji import validate_registry, list_loaded_keys


def setup_logging(level: str) -> None:
    fmt = "%(asctime)s.%(msecs)03d %(levelname)s %(name)s:%(lineno)d | %(message)s"
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


async def amain() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    log = logging.getLogger("main")

    # Validate premium emoji registry early so admins see problems in logs
    try:
        warnings = validate_registry()
        if warnings:
            for w in warnings:
                log.warning("emoji registry: %s", w)
        else:
            keys = list_loaded_keys()
            log.info("emoji registry: OK (%d entries)", len(keys))
    except Exception as e:
        log.warning("emoji registry validation failed: %s", e)

    if not settings.bot_token:
        raise SystemExit(
            "BOT_TOKEN is not set. Copy .env.example to .env and fill BOT_TOKEN."
        )

    log.info("Initialising database (%s)", settings.database_url)
    await init_db()
    async with SessionLocal() as session:
        n = await seed_products(session)
        if n:
            log.info("Seeded %d new products", n)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    dp.update.middleware(DbSessionMiddleware())
    # The banned-user middleware needs the DB session, so register after.
    dp.message.middleware(BannedUserMiddleware())
    dp.callback_query.middleware(BannedUserMiddleware())
    dp.callback_query.middleware(CallbackThrottleMiddleware(min_interval=0.4))

    dp.include_router(admin_handler.router)  # before start so admin commands take priority
    dp.include_router(start_handler.router)
    dp.include_router(shop_handler.router)
    dp.include_router(profile_handler.router)
    dp.include_router(support_handler.router)
    dp.include_router(refer_handler.router)

    me = await bot.get_me()
    if not settings.bot_username and me.username:
        # Cache the username on the in-memory settings instance so referral
        # links work without requiring the user to fill BOT_USERNAME in .env.
        settings.bot_username = me.username
    log.info("Bot @%s ready (id=%s). Starting long polling…", me.username, me.id)

    # Publish the slash-command menu so users see suggestions in Telegram.
    await _publish_commands(bot, settings.admin_ids)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        log.warning("delete_webhook failed: %s", e)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


_USER_COMMANDS = [
    BotCommand(command="start", description="Open the main menu"),
    BotCommand(command="menu", description="Open the main menu"),
    BotCommand(command="help", description="How to use this bot"),
]

_ADMIN_COMMANDS = _USER_COMMANDS + [
    BotCommand(command="admin", description="Admin help"),
    BotCommand(command="products", description="List products"),
    BotCommand(command="addproduct", description="slug|Name|emoji|duration|price"),
    BotCommand(command="setprice", description="slug 24.99"),
    BotCommand(command="setactive", description="slug on|off"),
    BotCommand(command="setdesc", description="slug Description..."),
    BotCommand(command="setemoji", description="slug ID|clear — premium emoji"),
    BotCommand(command="addstock", description="slug → then send .txt"),
    BotCommand(command="stock", description="slug — show available stock"),
    BotCommand(command="clearstock", description="slug confirm — delete unsold stock"),
    BotCommand(command="delproduct", description="slug confirm — delete a product"),
    BotCommand(command="withdrawals", description="Pending withdrawals"),
    BotCommand(command="approve_wd", description="ID [note]"),
    BotCommand(command="reject_wd", description="ID reason"),
    BotCommand(command="payments", description="Payment verification audit"),
    BotCommand(command="approve_pay", description="ID [note]"),
    BotCommand(command="reject_pay", description="ID reason"),
    BotCommand(command="whois", description="USER_ID"),
    BotCommand(command="credit", description="USER_ID 10.00 [note]"),
    BotCommand(command="debit", description="USER_ID 5.00 [note]"),
    BotCommand(command="ban", description="USER_ID"),
    BotCommand(command="unban", description="USER_ID"),
    BotCommand(command="broadcast", description="Send a message to every user"),
    BotCommand(command="getemoji", description="Reply to capture custom_emoji_ids"),
    BotCommand(command="reload_emojis", description="Re-read assets/premium_emojis.json"),
    BotCommand(command="stats", description="Bot stats"),
]


async def _publish_commands(bot: Bot, admin_ids: list[int]) -> None:
    log = logging.getLogger("main")
    try:
        await bot.set_my_commands(_USER_COMMANDS, scope=BotCommandScopeAllPrivateChats())
    except Exception as e:
        log.warning("set_my_commands (default) failed: %s", e)
    for aid in admin_ids:
        try:
            await bot.set_my_commands(_ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=aid))
        except Exception as e:
            log.warning("set_my_commands (admin %s) failed: %s", aid, e)


def main() -> None:
    try:
        asyncio.run(amain())
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
