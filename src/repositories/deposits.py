"""Persistence and atomic wallet crediting for verified deposits."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    BinanceOrder,
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
    if method == "bep20":
        return await session.scalar(
            select(TxidVerification.id).where(
                TxidVerification.txid == reference.strip().lower()
            )
        ) is not None
    return await session.scalar(
        select(BinanceOrder.id).where(BinanceOrder.order_id == reference.strip())
    ) is not None


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
