"""Lightweight per-user throttle on callback queries."""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, TelegramObject


class CallbackThrottleMiddleware(BaseMiddleware):
    def __init__(self, min_interval: float = 0.4) -> None:
        self.min_interval = min_interval
        self._last: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, CallbackQuery) and event.from_user is not None:
            now = time.monotonic()
            last = self._last.get(event.from_user.id, 0.0)
            if now - last < self.min_interval:
                # Silently ack to clear the spinner; do nothing else.
                try:
                    await event.answer()
                except Exception:
                    pass
                return None
            self._last[event.from_user.id] = now
        return await handler(event, data)
