"""Automatic shop payment verification clients.

These helpers verify a submitted payment reference against the external
provider only. The Telegram handler owns database status changes and delivery,
so a provider/API failure never crashes the bot or auto-rejects the customer.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

import aiohttp

from ..config import get_settings
from typing import Protocol

log = logging.getLogger(__name__)

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
USDT_DECIMALS = Decimal(10) ** 18
BEP20_HASH_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")
BINANCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{5,127}$")


@dataclass(frozen=True)
class ProviderResult:
    ok: bool
    retryable: bool = False
    detail: str = ""
    payload: object | None = None


def detect_id_type(value: str) -> str:
    """Classify a submitted payment reference without using screen state."""
    reference = value.strip()
    if BEP20_HASH_RE.fullmatch(reference):
        return "bep20_hash"
    if BINANCE_ID_RE.fullmatch(reference):
        return "binance_order_id"
    return "unknown"


def _as_decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


class VerificationTarget(Protocol):
    id: int
    expected_amount_usdt: Decimal
    received_amount_usdt: Decimal | None
    verification_note: str


def _amount_expected(payment: VerificationTarget) -> Decimal:
    return Decimal(str(payment.expected_amount_usdt)).quantize(Decimal("0.01"))


def _short(value: object, *, keep: int = 10) -> str:
    text = str(value or "")
    if len(text) <= keep * 2 + 3:
        return text
    return f"{text[:keep]}...{text[-keep:]}"


def _binance_secret() -> str:
    settings = get_settings()
    return settings.binance_api_secret or settings.binance_secret_key


def _signed_query(params: dict[str, object], secret: str) -> str:
    query = urlencode(params)
    signature = hmac.new(secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{query}&signature={signature}"


def _matches_binance_reference(row: dict, reference: str) -> bool:
    wanted = reference.strip()
    for key in ("orderId", "transactionId"):
        candidate = row.get(key)
        if candidate is not None and str(candidate).strip() == wanted:
            return True
    return False


def _binance_rows(payload: object) -> list[dict]:
    if isinstance(payload, dict):
        rows = payload.get("data") or payload.get("rows") or payload.get("list") or []
    else:
        rows = payload
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


async def verify_binance_payment(order_or_tx_id: str, payment: VerificationTarget) -> bool:
    """Verify a Binance Pay / UID transfer via ``GET /sapi/v1/pay/transactions``.

    On success this mutates ``payment.received_amount_usdt`` and
    ``payment.verification_note`` for the caller to persist through the existing
    payment repository/delivery flow.
    """
    reference = order_or_tx_id.strip()
    settings = get_settings()
    if not settings.binance_api_key or not _binance_secret():
        payment.verification_note = "Binance API credentials are not configured."
        log.warning("payment_verify binance not_configured payment_id=%s", payment.id)
        return False

    expected = _amount_expected(payment)
    end_ms = int(time.time() * 1000)
    lookback_hours = max(24, int(settings.payment_lookback_hours or 48))
    start_ms = int((datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).timestamp() * 1000)

    for attempt in range(1, 3):
        result = await _query_binance_pay_transactions(
            reference=reference,
            expected=expected,
            start_ms=start_ms,
            end_ms=end_ms,
        )
        log.info(
            "payment_verify binance attempt=%s payment_id=%s reference=%s ok=%s retryable=%s detail=%s",
            attempt,
            payment.id,
            _short(reference),
            result.ok,
            result.retryable,
            result.detail,
        )
        if result.ok and isinstance(result.payload, dict):
            amount = _as_decimal(result.payload.get("amount")) or expected
            payment.received_amount_usdt = amount.quantize(Decimal("0.01"))
            setattr(payment, "verification_payload", result.payload)
            payment.verification_note = (
                "auto matched Binance Pay transaction; "
                f"orderId={result.payload.get('orderId')}; "
                f"transactionId={result.payload.get('transactionId')}; "
                f"orderType={result.payload.get('orderType')}; ref:{reference}"
            )
            return True
        payment.verification_note = result.detail or "Binance transaction was not verified."
        if attempt == 1 and result.retryable:
            await asyncio.sleep(4)

    return False


async def _query_binance_pay_transactions(
    *,
    reference: str,
    expected: Decimal,
    start_ms: int,
    end_ms: int,
) -> ProviderResult:
    settings = get_settings()
    secret = _binance_secret()
    params = {
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": 100,
        "recvWindow": 10000,
        "timestamp": int(time.time() * 1000),
    }
    url = (
        f"{settings.binance_api_base_url.rstrip('/')}/sapi/v1/pay/transactions"
        f"?{_signed_query(params, secret)}"
    )
    try:
        async with aiohttp.ClientSession() as http:
            async with http.get(
                url,
                headers={"X-MBX-APIKEY": settings.binance_api_key},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                payload = await response.json(content_type=None)
                if response.status in {418, 429} or response.status >= 500:
                    return ProviderResult(False, True, f"Binance API temporary error {response.status}.", payload)
                if response.status >= 400:
                    detail = payload.get("msg") if isinstance(payload, dict) else None
                    return ProviderResult(False, False, str(detail or f"Binance API error {response.status}."), payload)
    except (aiohttp.ClientError, TimeoutError) as exc:
        log.warning("payment_verify binance network_error reference=%s error=%s", _short(reference), exc)
        return ProviderResult(False, True, "Binance API network error.", None)

    if not isinstance(payload, (dict, list)):
        return ProviderResult(False, True, "Binance API returned an invalid response.", payload)
    if isinstance(payload, dict) and payload.get("success") is False:
        return ProviderResult(False, True, str(payload.get("message") or "Binance API request failed."), payload)

    rows = _binance_rows(payload)
    for row in rows:
        if not _matches_binance_reference(row, reference):
            continue
        status = str(row.get("status") or "").upper()
        if status and status != "SUCCESS":
            return ProviderResult(False, True, f"Binance transaction status is {status}.", row)
        currency = str(row.get("currency") or "").upper()
        if currency and currency != "USDT":
            return ProviderResult(False, False, f"Binance transaction currency is {currency}, not USDT.", row)
        receiver = row.get("receiverInfo") if isinstance(row.get("receiverInfo"), dict) else {}
        receiver_uid = str(
            receiver.get("binanceId")
            or receiver.get("uid")
            or receiver.get("accountId")
            or ""
        )
        if settings.binance_uid and receiver_uid and receiver_uid != settings.binance_uid:
            return ProviderResult(False, False, "Binance transaction receiver UID does not match.", row)
        amount = _as_decimal(row.get("amount"))
        if amount is None:
            return ProviderResult(False, True, "Binance transaction amount is missing.", row)
        if amount.quantize(Decimal("0.01")) < expected:
            return ProviderResult(False, False, f"Binance amount {amount} is below expected {expected}.", row)
        return ProviderResult(True, False, "matched", row)

    return ProviderResult(False, True, "No matching Binance Pay transaction found yet.", payload)


async def verify_bep20_payment(tx_hash: str, payment: VerificationTarget) -> bool:
    """Verify a USDT BEP20 transfer by hash through BscScan proxy endpoints."""
    txid = tx_hash.strip().lower()
    expected = _amount_expected(payment)

    for attempt in range(1, 5):
        result = await _query_bscscan_transfer(txid, expected)
        log.info(
            "payment_verify bep20 attempt=%s payment_id=%s tx=%s ok=%s retryable=%s detail=%s",
            attempt,
            payment.id,
            _short(txid),
            result.ok,
            result.retryable,
            result.detail,
        )
        if result.ok and isinstance(result.payload, dict):
            payment.received_amount_usdt = Decimal(str(result.payload["amount"])).quantize(Decimal("0.01"))
            setattr(payment, "verification_payload", result.payload)
            payment.verification_note = (
                "auto matched BEP20 USDT transfer; "
                f"confirmations={result.payload.get('confirmations')}; ref:{txid}"
            )
            return True
        payment.verification_note = result.detail or "BEP20 transaction was not verified."
        if result.retryable and attempt < 4:
            await asyncio.sleep(8)
            continue
        break

    return False


async def _query_bscscan_transfer(txid: str, expected: Decimal) -> ProviderResult:
    settings = get_settings()
    wallet = (settings.bep20_wallet_address or settings.receiving_wallet_address).strip().lower()
    contract = (settings.usdt_bep20_contract or settings.bep20_usdt_contract).strip().lower()
    if len(txid) != 66 or not txid.startswith("0x"):
        return ProviderResult(False, False, "Invalid BEP20 transaction hash.")
    if len(wallet) != 42 or not wallet.startswith("0x"):
        return ProviderResult(False, False, "Receiving wallet address is not configured.")
    if len(contract) != 42 or not contract.startswith("0x"):
        return ProviderResult(False, False, "USDT BEP20 contract is not configured.")

    receipt = await _bscscan_proxy("eth_getTransactionReceipt", txhash=txid)
    if not receipt.ok:
        return receipt
    if receipt.payload is None:
        return ProviderResult(False, True, "BEP20 transaction is not mined yet.")
    if not isinstance(receipt.payload, dict):
        return ProviderResult(False, True, "BscScan returned an invalid receipt.")
    if receipt.payload.get("status") != "0x1":
        return ProviderResult(False, True, "BEP20 transaction failed or is not confirmed.")

    latest = await _bscscan_proxy("eth_blockNumber")
    if not latest.ok or not isinstance(latest.payload, str):
        return latest
    block_hex = receipt.payload.get("blockNumber")
    if not block_hex:
        return ProviderResult(False, True, "BEP20 transaction block is not available yet.")
    confirmations = max(0, int(latest.payload, 16) - int(str(block_hex), 16) + 1)
    required = max(3, min(int(settings.deposit_required_confirmations or 3), 6))
    if confirmations < required:
        return ProviderResult(False, True, f"Only {confirmations}/{required} confirmations received.")

    for row in receipt.payload.get("logs", []):
        if not isinstance(row, dict):
            continue
        topics = row.get("topics") or []
        if (
            str(row.get("address") or "").lower() == contract
            and len(topics) >= 3
            and str(topics[0]).lower() == TRANSFER_TOPIC
            and f"0x{str(topics[2])[-40:]}".lower() == wallet
        ):
            amount = Decimal(int(str(row.get("data") or "0x0"), 16)) / USDT_DECIMALS
            if amount.quantize(Decimal("0.01")) < expected:
                return ProviderResult(False, False, f"BEP20 amount {amount} is below expected {expected}.", row)
            return ProviderResult(
                True,
                False,
                "matched",
                {
                    "amount": amount,
                    "confirmations": confirmations,
                    "wallet": wallet,
                    "contract": contract,
                    "receipt": receipt.payload,
                },
            )

    return ProviderResult(False, False, "No USDT BEP20 transfer to the configured wallet was found.")


async def _bscscan_proxy(action: str, **params: object) -> ProviderResult:
    settings = get_settings()
    query: dict[str, object] = {
        "module": "proxy",
        "action": action,
        "apikey": settings.bscscan_api_key,
        **params,
    }
    try:
        async with aiohttp.ClientSession() as http:
            async with http.get(
                settings.bscscan_api_base_url,
                params=query,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                payload = await response.json(content_type=None)
                if response.status == 429 or response.status >= 500:
                    return ProviderResult(False, True, f"BscScan temporary error {response.status}.", payload)
                if response.status >= 400:
                    return ProviderResult(False, True, f"BscScan API error {response.status}.", payload)
    except (aiohttp.ClientError, TimeoutError) as exc:
        log.warning("payment_verify bscscan network_error action=%s error=%s", action, exc)
        return ProviderResult(False, True, "BscScan API network error.", None)

    if not isinstance(payload, dict):
        return ProviderResult(False, True, "BscScan returned an invalid response.", payload)
    if payload.get("error"):
        return ProviderResult(False, True, str(payload.get("error")), payload)
    message = str(payload.get("message") or "")
    if message.upper() == "NOTOK":
        return ProviderResult(False, True, str(payload.get("result") or "BscScan request failed."), payload)
    return ProviderResult(True, False, "ok", payload.get("result"))
