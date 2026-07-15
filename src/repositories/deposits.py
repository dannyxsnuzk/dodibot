"""Persistence and atomic wallet crediting for verified deposits."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    BinanceOrder,
    DepositReferenceClaim,
    DepositOrder,
    Transaction,
    TxidVerification,
    User,
    VerificationLog,
    WalletTransaction,
)
from ..services.deposit_verification import Bep20Match, BinanceOrderMatch


class DepositAlreadyUsed(Exception):
    pass


class DepositManualReviewUnavailable(Exception):
    pass


_RETRYABLE_BEP20_FAILURES = {
    "not_configured",
    "not_confirmed",
    "not_found",
    "provider_error",
    "wrong_recipient",
}


async def create_deposit(
    session: AsyncSession,
    *,
    user_id: int,
    method: str,
    expected_amount: Decimal | None,
) -> DepositOrder:
    order = DepositOrder(
        user_id=user_id,
        method=method,
        expected_amount=expected_amount,
        currency="USDT",
        status="pending",
    )
    session.add(order)
    await session.commit()
    await session.refresh(order)
    return order


async def get_deposit(
    session: AsyncSession, deposit_id: int, user_id: int | None = None
) -> DepositOrder | None:
    stmt = select(DepositOrder).where(DepositOrder.id == deposit_id)
    if user_id is not None:
        stmt = stmt.where(DepositOrder.user_id == user_id)
    return await session.scalar(stmt)


async def reference_is_used(
    session: AsyncSession, *, method: str, reference: str
) -> bool:
    provider = "bsc" if method == "bep20" else "binance_pay"
    normalized = reference.strip().lower() if provider == "bsc" else reference.strip()
    if await session.scalar(
        select(DepositReferenceClaim.id).where(
            DepositReferenceClaim.provider == provider,
            DepositReferenceClaim.reference == normalized,
        )
    ) is not None:
        return True
    if method == "bep20":
        return await session.scalar(
            select(TxidVerification.id).where(
                TxidVerification.txid == normalized
            )
        ) is not None
    return await session.scalar(
        select(BinanceOrder.id).where(BinanceOrder.order_id == normalized)
    ) is not None


async def reference_has_been_submitted(
    session: AsyncSession, *, reference: str
) -> bool:
    """Reject re-submissions, including failed attempts awaiting manual review."""
    normalized = reference.strip()
    if normalized.lower().startswith("0x"):
        condition = func.lower(DepositOrder.reference) == normalized.lower()
    else:
        condition = DepositOrder.reference == normalized
    return await session.scalar(select(DepositOrder.id).where(condition)) is not None


async def find_retryable_bep20_deposit(
    session: AsyncSession,
    *,
    user_id: int,
    txid: str,
    retry_window_minutes: int = 30,
) -> DepositOrder | None:
    """Return a user's recent, retry-safe failed BSC deposit attempt."""
    order = await session.scalar(
        select(DepositOrder)
        .where(
            DepositOrder.user_id == user_id,
            DepositOrder.method == "bep20",
            func.lower(DepositOrder.reference) == txid.strip().lower(),
            DepositOrder.status == "rejected",
            DepositOrder.rejection_code.in_(_RETRYABLE_BEP20_FAILURES),
        )
        .order_by(DepositOrder.id.desc())
    )
    if order is None or order.created_at is None:
        return None
    created_at = order.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if created_at < datetime.now(timezone.utc) - timedelta(minutes=retry_window_minutes):
        return None
    return order


async def submit_for_manual_review(
    session: AsyncSession, order: DepositOrder
) -> bool:
    if order.status != "rejected" or order.rejection_code == "already_used" or not order.reference:
        return False
    order.status = "manual_review"
    session.add(VerificationLog(
        deposit_order_id=order.id,
        user_id=order.user_id,
        provider="manual",
        reference=order.reference,
        outcome="manual_review",
        detail="Submitted by user for admin review.",
    ))
    await session.commit()
    return True


async def manually_credit_deposit(
    session: AsyncSession,
    order: DepositOrder,
    *,
    amount: Decimal,
    note: str,
) -> Decimal:
    if order.status != "manual_review" or not order.reference or amount <= 0:
        raise DepositManualReviewUnavailable("Deposit is not awaiting manual review.")

    reference = order.reference.strip()
    provider = "bsc" if reference.lower().startswith("0x") and len(reference) == 66 else "binance_pay"
    normalized = reference.lower() if provider == "bsc" else reference
    method = "bep20" if provider == "bsc" else "binance_uid"
    if await reference_is_used(session, method=method, reference=normalized):
        raise DepositAlreadyUsed("Reference was already credited or approved.")
    now = datetime.now(timezone.utc)
    try:
        session.add(DepositReferenceClaim(
            deposit_order_id=order.id,
            provider=provider,
            reference=normalized,
        ))
        await session.flush()
        user = await session.scalar(
            select(User).where(User.id == order.user_id).with_for_update()
        )
        if user is None:
            raise DepositManualReviewUnavailable("User no longer exists.")
        balance_after = Decimal(str(user.balance_usdt or 0)) + amount
        user.balance_usdt = balance_after
        session.add(WalletTransaction(
            user_id=order.user_id,
            deposit_order_id=order.id,
            kind="deposit",
            amount_usdt=amount,
            balance_after=balance_after,
            idempotency_key=f"manual:{provider}:{normalized}",
            note=note,
        ))
        session.add(Transaction(
            user_id=order.user_id,
            kind="deposit",
            amount_usdt=amount,
            ref_id=order.id,
            note=note,
        ))
        order.reference = normalized
        order.external_transaction_id = normalized
        order.received_amount = amount
        order.status = "verified"
        order.rejection_code = ""
        order.verified_at = now
        session.add(VerificationLog(
            deposit_order_id=order.id,
            user_id=order.user_id,
            provider="manual",
            reference=normalized,
            outcome="manual_approved",
            detail=f"Admin credited {amount} USDT. {note}".strip(),
        ))
        await session.commit()
        return balance_after
    except IntegrityError as exc:
        await session.rollback()
        raise DepositAlreadyUsed("Reference was already credited or approved.") from exc


async def reject_manual_review(
    session: AsyncSession, order: DepositOrder, *, note: str
) -> bool:
    if order.status != "manual_review":
        return False
    order.status = "manual_rejected"
    session.add(VerificationLog(
        deposit_order_id=order.id,
        user_id=order.user_id,
        provider="manual",
        reference=order.reference or "",
        outcome="manual_rejected",
        detail=note,
    ))
    await session.commit()
    return True


async def reject_deposit(
    session: AsyncSession,
    order: DepositOrder,
    *,
    reference: str,
    code: str,
    detail: str,
    provider: str,
    response: dict | None = None,
) -> None:
    order.reference = reference
    order.status = "rejected"
    order.rejection_code = code
    session.add(VerificationLog(
        deposit_order_id=order.id,
        user_id=order.user_id,
        provider=provider,
        reference=reference,
        outcome=code,
        detail=detail,
        response_payload=_json(response),
    ))
    await session.commit()


async def finalize_binance(
    session: AsyncSession,
    order: DepositOrder,
    *,
    submitted_order_id: str,
    match: BinanceOrderMatch,
) -> Decimal:
    provider_record = BinanceOrder(
        deposit_order_id=order.id,
        user_id=order.user_id,
        order_id=submitted_order_id,
        transaction_id=match.transaction_id or None,
        amount=match.amount,
        currency=match.currency,
        status=match.status,
        paid_at=match.paid_at,
        raw_response=_json(match.raw),
    )
    return await _finalize(
        session,
        order,
        reference=submitted_order_id,
        external_transaction_id=match.transaction_id,
        amount=match.amount,
        provider="binance_pay",
        provider_record=provider_record,
        response=match.raw,
    )


async def finalize_bep20(
    session: AsyncSession,
    order: DepositOrder,
    *,
    match: Bep20Match,
) -> Decimal:
    provider_record = TxidVerification(
        deposit_order_id=order.id,
        user_id=order.user_id,
        txid=match.txid,
        block_number=match.block_number,
        confirmations=match.confirmations,
        from_address=match.from_address,
        to_address=match.to_address,
        token_address=match.token_address,
        amount=match.amount,
        currency="USDT",
        status="confirmed",
        raw_response=_json(match.raw),
    )
    return await _finalize(
        session,
        order,
        reference=match.txid,
        external_transaction_id=match.txid,
        amount=match.amount,
        provider="bsc",
        provider_record=provider_record,
        response=match.raw,
    )


async def _finalize(
    session: AsyncSession,
    order: DepositOrder,
    *,
    reference: str,
    external_transaction_id: str,
    amount: Decimal,
    provider: str,
    provider_record: BinanceOrder | TxidVerification,
    response: dict,
) -> Decimal:
    now = datetime.now(timezone.utc)
    idempotency_key = f"{provider}:{reference.lower()}"
    deposit_id = order.id
    try:
        normalized = reference.lower() if provider == "bsc" else reference
        session.add(DepositReferenceClaim(
            deposit_order_id=order.id,
            provider=provider,
            reference=normalized,
        ))
        session.add(provider_record)
        await session.flush()  # unique reference gate before touching the wallet
        user = await session.scalar(
            select(User).where(User.id == order.user_id).with_for_update()
        )
        if user is None:
            raise ValueError(f"User {order.user_id} not found")
        balance_after = Decimal(str(user.balance_usdt or 0)) + amount
        user.balance_usdt = balance_after
        session.add(WalletTransaction(
            user_id=order.user_id,
            deposit_order_id=order.id,
            kind="deposit",
            amount_usdt=amount,
            balance_after=balance_after,
            idempotency_key=idempotency_key,
            note=f"{provider} deposit {reference}",
        ))
        session.add(Transaction(
            user_id=order.user_id,
            kind="deposit",
            amount_usdt=amount,
            ref_id=order.id,
            note=f"{provider} deposit {reference}",
        ))
        order.reference = reference
        order.external_transaction_id = external_transaction_id
        order.received_amount = amount
        order.status = "verified"
        order.rejection_code = ""
        order.verified_at = now
        session.add(VerificationLog(
            deposit_order_id=order.id,
            user_id=order.user_id,
            provider=provider,
            reference=reference,
            outcome="verified",
            detail=f"Credited {amount} USDT",
            response_payload=_json(response),
        ))
        await session.commit()
        return balance_after
    except IntegrityError as exc:
        await session.rollback()
        duplicate = await get_deposit(session, deposit_id)
        if duplicate is not None and duplicate.status == "pending":
            await reject_deposit(
                session,
                duplicate,
                reference=reference,
                code="already_used",
                detail="Reference was already credited.",
                provider=provider,
            )
        raise DepositAlreadyUsed("Reference was already used.") from exc


def _json(value: dict | None) -> str:
    if not value:
        return ""
    return json.dumps(value, separators=(",", ":"), default=str)[:50000]
