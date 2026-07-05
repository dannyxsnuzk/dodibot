"""External verification clients for Binance Pay and BEP20 USDT."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import string
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

import aiohttp

from .deposit_settings import DepositSettings

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
TXID_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")
ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


class DepositVerificationError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class BinanceOrderMatch:
    order_id: str
    transaction_id: str
    amount: Decimal
    currency: str
    status: str
    paid_at: datetime
    raw: dict


@dataclass(frozen=True)
class Bep20Match:
    txid: str
    block_number: int
    confirmations: int
    from_address: str
    to_address: str
    token_address: str
    amount: Decimal
    raw: dict


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DepositVerificationError("invalid_response", "Provider returned an invalid amount.") from exc


def _nonce() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(32))


async def query_binance_pay_order(
    order_id: str,
    settings: DepositSettings,
) -> BinanceOrderMatch:
    """Query an order created under the configured Binance Pay merchant account."""
    if not settings.binance_pay_api_key or not settings.binance_pay_secret:
        raise DepositVerificationError(
            "not_configured", "Binance Pay merchant API credentials are not configured."
        )
    cleaned = order_id.strip()
    if not cleaned or len(cleaned) > 32 or not cleaned.isalnum():
        raise DepositVerificationError("invalid_order_id", "Invalid Binance Pay Order ID.")

    candidates: list[dict[str, str | None]] = []
    if len(cleaned) <= 19:
        candidates.append({"prepayId": cleaned, "merchantTradeNo": None})
    candidates.append({"merchantTradeNo": cleaned, "prepayId": None})
    last_error = "Order not found."
    for body in candidates:
        data = await _binance_pay_post(
            "/binancepay/openapi/v2/order/query", body, settings
        )
        if str(data.get("status", "")).upper() != "SUCCESS":
            last_error = str(data.get("errorMessage") or data.get("msg") or last_error)
            continue
        order = data.get("data")
        if not isinstance(order, dict):
            continue
        order_status = str(order.get("status", "")).upper()
        if order_status != "PAID":
            raise DepositVerificationError(
                "not_success", f"Binance Pay order status is {order_status or 'unknown'}."
            )
        currency = str(order.get("currency", "")).upper()
        if currency != "USDT":
            raise DepositVerificationError("wrong_currency", "Only USDT deposits are accepted.")
        timestamp = order.get("transactTime")
        if not timestamp:
            raise DepositVerificationError("invalid_response", "Paid time is missing.")
        paid_at = datetime.fromtimestamp(int(timestamp) / 1000, tz=timezone.utc)
        return BinanceOrderMatch(
            order_id=str(order.get("prepayId") or order.get("merchantTradeNo") or cleaned),
            transaction_id=str(order.get("transactionId") or ""),
            amount=_decimal(order.get("totalFee")),
            currency=currency,
            status=order_status,
            paid_at=paid_at,
            raw=data,
        )
    raise DepositVerificationError("not_found", last_error)


async def _binance_pay_post(
    path: str,
    body: dict[str, object],
    settings: DepositSettings,
) -> dict:
    encoded = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    timestamp = str(int(time.time() * 1000))
    nonce = _nonce()
    payload = f"{timestamp}\n{nonce}\n{encoded}\n"
    signature = hmac.new(
        settings.binance_pay_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha512,
    ).hexdigest().upper()
    headers = {
        "Content-Type": "application/json",
        "BinancePay-Timestamp": timestamp,
        "BinancePay-Nonce": nonce,
        "BinancePay-Certificate-SN": settings.binance_pay_api_key,
        "BinancePay-Signature": signature,
    }
    url = f"{settings.binance_pay_api_base_url.rstrip('/')}{path}"
    try:
        async with aiohttp.ClientSession() as http:
            async with http.post(
                url,
                data=encoded.encode("utf-8"),
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                data = await response.json(content_type=None)
                if not isinstance(data, dict):
                    raise DepositVerificationError(
                        "provider_error", "Binance Pay returned an invalid response."
                    )
                if response.status >= 500:
                    raise DepositVerificationError(
                        "provider_error", "Binance Pay is temporarily unavailable."
                    )
                return data
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise DepositVerificationError(
            "provider_error", "Could not reach Binance Pay. Please try again."
        ) from exc


async def query_binance_pay_transaction(
    transaction_id: str,
    settings: DepositSettings,
) -> BinanceOrderMatch:
    """Find an incoming C2C transfer in the configured Binance account history."""
    if not settings.binance_api_key or not settings.binance_secret:
        raise DepositVerificationError(
            "not_configured", "Binance account API credentials are not configured."
        )
    cleaned = transaction_id.strip()
    if not cleaned or len(cleaned) > 128:
        raise DepositVerificationError("invalid_order_id", "Invalid Binance Pay Order ID.")
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - settings.allowed_window_minutes * 60 * 1000
    params: dict[str, object] = {
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": 100,
        "recvWindow": 10000,
        "timestamp": end_ms,
    }
    query = urlencode(params)
    signature = hmac.new(
        settings.binance_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    url = (
        f"{settings.binance_api_base_url.rstrip('/')}/sapi/v1/pay/transactions"
        f"?{query}&signature={signature}"
    )
    try:
        async with aiohttp.ClientSession() as http:
            async with http.get(
                url,
                headers={"X-MBX-APIKEY": settings.binance_api_key},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                payload = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise DepositVerificationError(
            "provider_error", "Could not reach Binance Pay transaction history."
        ) from exc
    if response.status >= 400 or not isinstance(payload, dict):
        detail = payload.get("msg") if isinstance(payload, dict) else None
        raise DepositVerificationError(
            "provider_error", str(detail or "Binance Pay history request failed.")
        )
    if not payload.get("success") or str(payload.get("code")) != "000000":
        raise DepositVerificationError(
            "provider_error", str(payload.get("message") or "Binance Pay history request failed.")
        )
    for row in payload.get("data") or []:
        if not isinstance(row, dict) or str(row.get("transactionId")) != cleaned:
            continue
        amount = _decimal(row.get("amount"))
        if amount <= 0:
            raise DepositVerificationError("not_success", "The transaction is not incoming.")
        currency = str(row.get("currency") or "").upper()
        if currency != "USDT":
            raise DepositVerificationError("wrong_currency", "Only USDT deposits are accepted.")
        receiver = row.get("receiverInfo") or {}
        receiver_uid = str(receiver.get("binanceId") or "")
        if settings.binance_uid and receiver_uid and receiver_uid != settings.binance_uid:
            raise DepositVerificationError(
                "wrong_recipient", "Transaction recipient does not match the configured Binance UID."
            )
        paid_at = datetime.fromtimestamp(
            int(row.get("transactionTime")) / 1000, tz=timezone.utc
        )
        return BinanceOrderMatch(
            order_id=cleaned,
            transaction_id=cleaned,
            amount=amount,
            currency=currency,
            status="SUCCESS",
            paid_at=paid_at,
            raw=payload,
        )
    raise DepositVerificationError("not_found", "Binance Pay transaction was not found.")


async def verify_bep20_tx(
    txid: str,
    expected_amount: Decimal | None,
    settings: DepositSettings,
) -> Bep20Match:
    cleaned = txid.strip().lower()
    if not TXID_RE.fullmatch(cleaned):
        raise DepositVerificationError("invalid_txid", "Invalid BEP20 transaction hash.")
    if not ADDRESS_RE.fullmatch(settings.bep20_wallet_address):
        raise DepositVerificationError("not_configured", "BEP20 wallet address is not configured.")
    if not ADDRESS_RE.fullmatch(settings.bep20_usdt_contract):
        raise DepositVerificationError("not_configured", "BEP20 USDT contract is not configured.")

    receipt, transaction, latest_hex = await _rpc_batch(
        settings.bsc_rpc_url,
        [
            ("eth_getTransactionReceipt", [cleaned]),
            ("eth_getTransactionByHash", [cleaned]),
            ("eth_blockNumber", []),
        ],
    )
    if receipt is None or transaction is None:
        raise DepositVerificationError("not_found", "Transaction was not found on BNB Smart Chain.")
    if receipt.get("status") != "0x1":
        raise DepositVerificationError("not_confirmed", "Transaction failed or is not confirmed.")

    block_number = int(receipt.get("blockNumber", "0x0"), 16)
    latest = int(latest_hex, 16)
    confirmations = max(0, latest - block_number + 1)
    if confirmations < settings.required_confirmations:
        raise DepositVerificationError(
            "not_confirmed",
            f"Only {confirmations}/{settings.required_confirmations} confirmations received.",
        )

    wallet = settings.bep20_wallet_address.lower()
    contract = settings.bep20_usdt_contract.lower()
    transfer_log: dict | None = None
    amount = Decimal("0")
    for row in receipt.get("logs", []):
        topics = row.get("topics") or []
        if (
            str(row.get("address", "")).lower() == contract
            and len(topics) >= 3
            and str(topics[0]).lower() == TRANSFER_TOPIC
            and f"0x{str(topics[2])[-40:]}".lower() == wallet
        ):
            transfer_log = row
            amount = Decimal(int(str(row.get("data", "0x0")), 16)) / Decimal(10**18)
            break
    if transfer_log is None:
        raise DepositVerificationError(
            "wrong_recipient", "No USDT transfer to the configured wallet was found."
        )
    if expected_amount is not None and amount != expected_amount:
        raise DepositVerificationError(
            "wrong_amount", f"Expected {expected_amount} USDT, received {amount} USDT."
        )
    return Bep20Match(
        txid=cleaned,
        block_number=block_number,
        confirmations=confirmations,
        from_address=str(transaction.get("from") or "").lower(),
        to_address=wallet,
        token_address=contract,
        amount=amount,
        raw={"receipt": receipt, "transaction": transaction},
    )


async def _rpc_batch(url: str, calls: list[tuple[str, list]]) -> list[object]:
    if not url:
        raise DepositVerificationError("not_configured", "BSC RPC URL is not configured.")
    payload = [
        {"jsonrpc": "2.0", "id": index, "method": method, "params": params}
        for index, (method, params) in enumerate(calls, start=1)
    ]
    try:
        async with aiohttp.ClientSession() as http:
            async with http.post(
                url, json=payload, timeout=aiohttp.ClientTimeout(total=20)
            ) as response:
                data = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise DepositVerificationError(
            "provider_error", "Could not reach the BNB Smart Chain RPC."
        ) from exc
    if response.status >= 400 or not isinstance(data, list):
        raise DepositVerificationError("provider_error", "BNB Smart Chain RPC returned an error.")
    indexed = {int(item.get("id")): item for item in data if isinstance(item, dict)}
    results: list[object] = []
    for index in range(1, len(calls) + 1):
        item = indexed.get(index, {})
        if item.get("error"):
            raise DepositVerificationError(
                "provider_error", str(item["error"].get("message") or "RPC error")
            )
        results.append(item.get("result"))
    return results
