"""Async SQLAlchemy engine + session factory."""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from .base import Base

settings = get_settings()

# Make sure the SQLite directory exists
if settings.database_url.startswith("sqlite"):
    db_path_str = settings.database_url.split("///", 1)[-1]
    db_path = Path(db_path_str)
    db_path.parent.mkdir(parents=True, exist_ok=True)

engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    """Create all tables. Idempotent — safe to call on every startup.

    Also runs forward-compatible column additions for users who upgrade
    from an older schema (SQLite only).
    """
    # Import models so they're registered on the metadata.
    from . import models  # noqa: F401
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Lightweight forward migrations for SQLite (no Alembic dependency).
        if settings.database_url.startswith("sqlite"):
            cols = await conn.execute(text("PRAGMA table_info(products)"))
            existing = {row[1] for row in cols.fetchall()}
            if "emoji_id" not in existing:
                await conn.execute(
                    text("ALTER TABLE products ADD COLUMN emoji_id VARCHAR(32)")
                )

            cols = await conn.execute(text("PRAGMA table_info(stock_items)"))
            existing = {row[1] for row in cols.fetchall()}
            if "reserved_by" not in existing:
                await conn.execute(
                    text("ALTER TABLE stock_items ADD COLUMN reserved_by BIGINT")
                )
            if "reserved_until" not in existing:
                await conn.execute(
                    text("ALTER TABLE stock_items ADD COLUMN reserved_until DATETIME")
                )


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
