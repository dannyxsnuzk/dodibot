"""Minimal, defensive client for the Canboso Telegram buyer API."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import aiohttp

from ..config import get_settings


class CanbosoError(Exception):
    """The supplier explicitly declined or could not fulfil an order."""


class CanbosoUnknownResult(CanbosoError):
    """The request may have reached the supplier; never retry automatically."""


@dataclass(frozen=True)
class CanbosoPurchase:
    order_code: str
    delivered_payload: str
    raw_response: str


def _api_url(path: str) -> str:
    base = get_settings().canboso_api_base_url.rstrip("/")
    if not base.startswith("https://"):
        raise CanbosoError("Canboso API URL must use HTTPS.")
    return f"{base}{path}"


def _buyer_key() -> str:
    key = get_settings().canboso_buyer_key.strip()
    if not key:
        raise CanbosoError("Canboso reseller API is not configured.")
    return key


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _delivery_payload(data: dict[str, Any]) -> str:
    delivered = data.get("deliveredAccounts")
    if not delivered:
        raise CanbosoError("Canboso returned no delivered account details.")
    if isinstance(delivered, str):
        content = delivered.strip()
    else:
        content = _compact_json(delivered)
    if not content:
        raise CanbosoError("Canboso returned empty delivery details.")
    order_code = str(data.get("orderCode") or "").strip()
    prefix = f"Canboso order: {order_code}\n\n" if order_code else ""
    return prefix + content


async def purchase(*, vendor_product_id: str, quantity: int) -> CanbosoPurchase:
    """Purchase once. Connection/timeout ambiguity is intentionally non-retryable."""
    if not vendor_product_id.strip() or quantity < 1:
        raise CanbosoError("Invalid Canboso product or quantity.")
    timeout_seconds = max(5, min(int(get_settings().canboso_request_timeout_seconds), 60))
    payload = {
        "key": _buyer_key(),
        "product_id": vendor_product_id.strip(),
        "quantity": quantity,
    }
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as client:
            async with client.post(_api_url("/api/telegram-buyer/purchase"), json=payload) as response:
                body = await response.text()
                if response.status >= 500:
                    raise CanbosoUnknownResult("Canboso is temporarily unavailable.")
                if response.status < 200 or response.status >= 300:
                    raise CanbosoError("Canboso rejected this purchase.")
    except (aiohttp.ClientConnectionError, aiohttp.ServerTimeoutError, TimeoutError) as exc:
        raise CanbosoUnknownResult("Canboso request outcome is unknown.") from exc
    except aiohttp.ClientError as exc:
        raise CanbosoUnknownResult("Canboso request outcome is unknown.") from exc

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise CanbosoUnknownResult("Canboso returned an invalid response.") from exc
    if not isinstance(data, dict):
        raise CanbosoUnknownResult("Canboso returned an invalid response.")
    if data.get("success") is False:
        raise CanbosoError("Canboso could not fulfil this product.")
    return CanbosoPurchase(
        order_code=str(data.get("orderCode") or "").strip(),
        delivered_payload=_delivery_payload(data),
        raw_response=_compact_json(data),
    )
