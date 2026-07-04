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
    """Serve the FastAPI dashboard without starting another process."""
    settings = get_settings()
    railway_port = os.getenv("PORT")
    host = "0.0.0.0" if railway_port else settings.dashboard_host
    port = int(railway_port) if railway_port else settings.dashboard_port
    config = uvicorn.Config(
        "src.admin_dashboard.server:app",
        host=host,
        port=port,
        log_level=settings.log_level.lower(),
        reload=False,
    )
    await uvicorn.Server(config).serve()


async def amain() -> None:
    tasks = {
        asyncio.create_task(run_bot(), name="telegram-bot"),
        asyncio.create_task(run_dashboard(), name="admin-dashboard"),
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
