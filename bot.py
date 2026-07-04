"""Run the Telegram bot and admin dashboard in one process.

Usage:
    pip install -r requirements.txt
    python bot.py
"""
from __future__ import annotations

import asyncio
import os

import uvicorn

from src.config import get_settings
from src.main import amain as run_bot


async def run_dashboard() -> None:
    """Serve the FastAPI dashboard from the application's only Uvicorn server."""
    settings = get_settings()
    config = uvicorn.Config(
        "src.admin_dashboard.server:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8088)),
        log_level=settings.log_level.lower(),
        reload=False,
    )
    await uvicorn.Server(config).serve()


async def amain() -> None:
    # Start the HTTP listener first so Railway can reach the service while the
    # Telegram bot performs database, Binance, and Telegram initialization.
    tasks = {
        asyncio.create_task(run_dashboard(), name="admin-dashboard"),
        asyncio.create_task(run_bot(), name="telegram-bot"),
    }
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    for task in done:
        exception = task.exception()
        if exception is not None:
            raise exception


def main() -> None:
    try:
        asyncio.run(amain())
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
