"""Binance deposit verification helpers."""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

import aiohttp

from ..config import get_settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BinancePaymentMatch:
    reference: str
    amount: Decimal
    asset: str
    raw: dict
    source: str


class BinanceVerificationError(Exception):
    pass


def _as_decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _matches_reference(row: dict, reference: str) -> bool:
    wanted = reference.strip()
    normalized = wanted[2:] if wanted.lower().startswith("0x") else wanted
    for key in ("txId", "txid"):
        value = row.get(key)
        if value is None:
            continue
        candidate = str(value).strip()
        candidate_normalized = candidate[2:] if candidate.lower().startswith("0x") else candidate
        if candidate == wanted or candidate_normalized.lower() == normalized.lower():
            return True
    return False


def _extract_amount(row: dict) -> Decimal | None:
    for key in ("amount", "orderAmount", "totalAmount", "receiveAmount"):
        amount = _as_decimal(row.get(key))
        if amount is not None:
            return amount
    return None


def _extract_asset(row: dict) -> str:
    for key in ("currency", "asset", "coin", "orderCurrency"):
        value = row.get(key)
        if value:
            return str(value).upper()
    return ""


def _short(value: object, *, keep: int = 8) -> str:
    text = str(value or "")
    if len(text) <= keep * 2 + 3:
        return text
    return f"{text[:keep]}...{text[-keep:]}"


def _summarize_row(row: dict) -> dict[str, str]:
    interesting_keys = (
        "txId",
        "txid",
        "addressTag",
        "amount",
        "orderAmount",
        "totalAmount",
        "receiveAmount",
        "currency",
        "asset",
        "coin",
        "orderCurrency",
        "insertTime",
        "successTime",
        "status",
    )
    return {key: _short(row[key]) for key in interesting_keys if key in row and row[key] is not None}


def _signed_query(params: dict[str, object], secret: str) -> str:
    query = urlencode(params)
    signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    return f"{query}&signature={signature}"


async def find_binance_payment(
    *,
    reference: str,
    expected_amount: Decimal,
    require_amount_match: bool = True,
    lookback_hours: int = 24,
) -> BinancePaymentMatch | None:
    """Search recent Binance deposit records for a matching blockchain TxID and amount.

    Auto-verification intentionally uses deposit history only.
    """
    settings = get_settings()
    if not settings.binance_api_key or not settings.binance_secret_key:
        raise BinanceVerificationError("Binance API key/secret are not configured.")

    end_ms = int(time.time() * 1000)
    start_dt = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    end_dt = datetime.now(timezone.utc)
    start_ms = int(start_dt.timestamp() * 1000)
    params = {
        "startTime": start_ms,
        "endTime": end_ms,
        "timestamp": end_ms,
        "recvWindow": 5000,
    }
    headers = {"X-MBX-APIKEY": settings.binance_api_key}
    endpoints = (("deposit", f"{settings.binance_api_base_url.rstrip('/')}/sapi/v1/capital/deposit/hisrec"),)
    async with aiohttp.ClientSession() as http:
        for source, url in endpoints:
            log.info(
                "binance lookup source=%s reference=%s expected=%s require_amount_match=%s window_utc=%s..%s",
                source,
                reference,
                expected_amount,
                require_amount_match,
                start_dt.isoformat(),
                end_dt.isoformat(),
            )
            async with http.get(
                f"{url}?{_signed_query(params, settings.binance_secret_key)}",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status >= 400:
                    log.warning("binance lookup source=%s error=%r", source, data)
                    continue
                log.info("binance lookup source=%s status=%s response_type=%s", source, resp.status, type(data).__name__)
                match = _match_rows(
                    data,
                    reference=reference,
                    expected_amount=expected_amount,
                    require_amount_match=require_amount_match,
                    source=source,
                )
                if match is not None:
                    log.info(
                        "binance lookup matched source=%s reference=%s amount=%s asset=%s",
                        source,
                        reference,
                        match.amount,
                        match.asset,
                    )
                    return match

    log.info("binance lookup no_match reference=%s lookback_hours=%s", reference, lookback_hours)
    return None


def _match_rows(
    data: object,
    *,
    reference: str,
    expected_amount: Decimal,
    require_amount_match: bool,
    source: str,
) -> BinancePaymentMatch | None:
    rows = data.get("data", data) if isinstance(data, dict) else data
    if isinstance(rows, dict):
        for key in ("rows", "list", "data"):
            if isinstance(rows.get(key), list):
                rows = rows[key]
                break
    if not isinstance(rows, list):
        log.info("binance lookup source=%s rows_unavailable shape=%s", source, type(rows).__name__)
        return None

    expected = expected_amount.quantize(Decimal("0.01"))
    log.info("binance lookup source=%s rows=%s reference=%s", source, len(rows), reference)
    for idx, row in enumerate(rows[:5], start=1):
        if isinstance(row, dict):
            log.info("binance lookup source=%s row_sample_%s=%s", source, idx, _summarize_row(row))
    for row in rows:
        if not isinstance(row, dict) or not _matches_reference(row, reference):
            continue
        asset = _extract_asset(row)
        if asset and asset != "USDT":
            log.info("binance lookup source=%s reference_match asset_rejected asset=%s", source, asset)
            continue
        amount = _extract_amount(row)
        if amount is None:
            log.info("binance lookup source=%s reference_match amount_missing", source)
            continue
        # Accept overpayments, but never accept a transaction whose received
        # amount is below the order total.
        if require_amount_match and amount.quantize(Decimal("0.01")) < expected:
            log.info(
                "binance lookup source=%s reference_match amount_rejected amount=%s expected=%s",
                source,
                amount,
                expected,
            )
            continue
        return BinancePaymentMatch(
            reference=reference,
            amount=amount.quantize(Decimal("0.01")),
            asset=asset or "USDT",
            raw=row,
            source=source,
        )
    return None
