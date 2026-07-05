"""Shared UX, validation, and verification dispatch for purchases and deposits."""
from __future__ import annotations

import re
import asyncio
import contextlib
from dataclasses import dataclass
from decimal import Decimal
from collections.abc import Awaitable, Callable
from typing import TypeVar

from .payment_verification import verify_bep20_payment, verify_binance_payment
from ..ui import texts

T = TypeVar("T")

_ETH_TX_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")
_HEX_TX_RE = re.compile(r"^[a-fA-F0-9]{64}$")
_BINANCE_PAY_TX_RE = re.compile(r"^P_[A-Z0-9]{8,}$", re.IGNORECASE)
_BINANCE_ORDER_RE = re.compile(r"^\d{12,24}$")


@dataclass
class VerificationTarget:
    id: int
    expected_amount_usdt: Decimal
    received_amount_usdt: Decimal | None = None
    verification_note: str = ""
    verification_payload: dict | None = None


def payment_instructions(provider: str, destination: str) -> str:
    if provider == "bep20":
        return texts.bep20_payment_instructions(
            name="", duration="", qty=1, price_each=Decimal("0"),
            wallet_address=destination,
        )
    return texts.binance_pay_instructions(
        name="", duration="", qty=1, price_each=Decimal("0"),
        binance_id=destination,
    )


def is_plausible_reference(provider: str, reference: str) -> bool:
    value = reference.strip()
    if provider == "bep20":
        return bool(_ETH_TX_RE.fullmatch(value))
    return bool(
        _ETH_TX_RE.fullmatch(value) or _HEX_TX_RE.fullmatch(value)
        or _BINANCE_PAY_TX_RE.fullmatch(value) or _BINANCE_ORDER_RE.fullmatch(value)
    )


def detect_reference_provider(reference: str) -> str | None:
    """Choose BSC only for a canonical 0x hash; otherwise try Binance IDs."""
    value = reference.strip()
    if _ETH_TX_RE.fullmatch(value):
        return "bep20"
    if is_plausible_reference("binance_pay", value):
        return "binance_pay"
    return None


async def verify_reference(provider: str, reference: str, target: VerificationTarget) -> bool:
    if provider == "bep20":
        return await verify_bep20_payment(reference, target)
    return await verify_binance_payment(reference, target)


def verifying_text(reference: str, pct: int, remaining: str) -> str:
    return texts.payment_verifying(
        reference=reference, progress_pct=pct, remaining_text=remaining
    )


def format_remaining(seconds: int) -> str:
    minutes, secs = divmod(max(0, int(seconds)), 60)
    return f"~{minutes}m {secs}s"


async def run_with_progress(
    reference: str,
    operation: Awaitable[T],
    update: Callable[[str], Awaitable[None]],
    *,
    wait_seconds: int = 60,
    interval_seconds: int = 10,
) -> T:
    """Run provider verification while continuously updating Telegram progress."""
    task = asyncio.create_task(operation)
    elapsed = 0
    await update(verifying_text(reference, 0, format_remaining(wait_seconds)))
    try:
        while not task.done():
            delay = min(interval_seconds, max(1, wait_seconds - elapsed))
            done, _ = await asyncio.wait({task}, timeout=delay)
            if done:
                break
            elapsed = min(wait_seconds, elapsed + delay)
            pct = min(99, int(elapsed / max(1, wait_seconds) * 100))
            await update(
                verifying_text(reference, pct, format_remaining(wait_seconds - elapsed))
            )
        return await task
    except BaseException:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        raise


async def retry_operation(
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int = 4,
    interval_seconds: int = 15,
) -> T:
    """Retry a verification lookup so pending chain/API results can settle."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return await operation()
        except Exception as exc:
            last_error = exc
            code = str(getattr(exc, "code", ""))
            if code and code not in {
                "not_found", "not_confirmed", "provider_error", "invalid_response"
            }:
                raise
            if attempt + 1 < attempts:
                await asyncio.sleep(interval_seconds)
    assert last_error is not None
    raise last_error
