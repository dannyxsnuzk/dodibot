from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import desc, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import PaymentReferenceClaim, PaymentVerification


class PaymentReferenceAlreadyUsed(Exception):
    pass


async def create_payment_verification(
    session: AsyncSession,
    *,
    user_id: int,
    product_id: int,
    provider: str,
    reference: str,
    qty: int,
    expected_amount_usdt: Decimal,
) -> PaymentVerification:
    rec = PaymentVerification(
        user_id=user_id,
        product_id=product_id,
        provider=provider,
        reference=reference,
        qty=qty,
        expected_amount_usdt=expected_amount_usdt,
        status="pending",
    )
    session.add(rec)
    try:
        await session.flush()
        session.add(PaymentReferenceClaim(
            payment_id=rec.id,
            provider=provider,
            reference=reference,
        ))
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise PaymentReferenceAlreadyUsed("Payment reference was already submitted.") from exc
    await session.refresh(rec)
    return rec


async def get_payment_verification(session: AsyncSession, payment_id: int) -> PaymentVerification | None:
    return await session.get(PaymentVerification, payment_id)


async def get_completed_reference(
    session: AsyncSession,
    *,
    provider: str,
    reference: str,
    exclude_id: int | None = None,
) -> PaymentVerification | None:
    statuses = ("auto_verified", "manual_approved", "delivered")
    stmt = (
        select(PaymentVerification)
        .where(
            PaymentVerification.provider == provider,
            PaymentVerification.reference == reference,
            PaymentVerification.status.in_(statuses),
        )
        .order_by(desc(PaymentVerification.id))
        .limit(1)
    )
    if exclude_id is not None:
        stmt = stmt.where(PaymentVerification.id != exclude_id)
    return await session.scalar(stmt)


async def get_reference(
    session: AsyncSession,
    *,
    reference: str,
    exclude_id: int | None = None,
) -> PaymentVerification | None:
    stmt = (
        select(PaymentVerification)
        .where(PaymentVerification.reference == reference)
        .order_by(desc(PaymentVerification.id))
        .limit(1)
    )
    if exclude_id is not None:
        stmt = stmt.where(PaymentVerification.id != exclude_id)
    return await session.scalar(stmt)


async def get_completed_reference_in_note(
    session: AsyncSession,
    *,
    provider: str,
    identifiers: list[str] | tuple[str, ...],
    exclude_id: int | None = None,
) -> PaymentVerification | None:
    statuses = ("auto_verified", "manual_approved", "delivered")
    stmt = (
        select(PaymentVerification)
        .where(
            PaymentVerification.provider == provider,
            PaymentVerification.status.in_(statuses),
        )
        .order_by(desc(PaymentVerification.id))
        .limit(1)
    )
    if exclude_id is not None:
        stmt = stmt.where(PaymentVerification.id != exclude_id)
    clauses = [
        PaymentVerification.verification_note.like(f"%ref:{identifier}%")
        for identifier in identifiers
        if identifier
    ]
    if not clauses:
        return None
    stmt = stmt.where(or_(*clauses))
    return await session.scalar(stmt)


async def bump_attempt(
    session: AsyncSession,
    payment: PaymentVerification,
    *,
    note: str = "",
) -> None:
    payment.attempts = int(payment.attempts or 0) + 1
    if note:
        payment.verification_note = note
    await session.commit()


async def mark_auto_verified(
    session: AsyncSession,
    payment: PaymentVerification,
    *,
    received_amount: Decimal,
    note: str,
) -> None:
    payment.status = "auto_verified"
    payment.received_amount_usdt = received_amount
    payment.verification_note = note
    payment.decided_at = datetime.now(timezone.utc)
    await session.commit()


async def mark_rejected(
    session: AsyncSession,
    payment: PaymentVerification,
    *,
    status: str,
    note: str,
    decided_by: int | None = None,
) -> None:
    payment.status = status
    payment.verification_note = note
    payment.decided_at = datetime.now(timezone.utc)
    payment.decided_by = decided_by
    await session.commit()


async def mark_manual_approved(
    session: AsyncSession,
    payment: PaymentVerification,
    *,
    admin_id: int,
    note: str,
    received_amount: Decimal | None = None,
) -> None:
    payment.status = "manual_approved"
    payment.decided_by = admin_id
    payment.decided_at = datetime.now(timezone.utc)
    payment.verification_note = note
    if received_amount is not None:
        payment.received_amount_usdt = received_amount
    await session.commit()


async def mark_delivered(
    session: AsyncSession,
    payment: PaymentVerification,
    *,
    order_ids: list[int],
) -> None:
    payment.status = "delivered"
    payment.order_ids_csv = ",".join(str(x) for x in order_ids)
    payment.decided_at = datetime.now(timezone.utc)
    await session.commit()


async def list_payment_verifications(
    session: AsyncSession,
    *,
    status: str | None = None,
    limit: int = 100,
) -> list[PaymentVerification]:
    stmt = select(PaymentVerification).order_by(desc(PaymentVerification.id)).limit(limit)
    if status and status != "all":
        stmt = (
            select(PaymentVerification)
            .where(PaymentVerification.status == status)
            .order_by(desc(PaymentVerification.id))
            .limit(limit)
        )
    return list((await session.scalars(stmt)).all())
