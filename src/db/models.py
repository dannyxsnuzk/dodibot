"""SQLAlchemy models for Batman Bot."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

NUMERIC = Numeric(18, 6)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # telegram user_id
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    balance_usdt: Mapped[Decimal] = mapped_column(NUMERIC, default=Decimal("0"))
    referral_code: Mapped[str] = mapped_column(String(16), unique=True)
    referred_by: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_menu_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    last_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(128))
    emoji: Mapped[str] = mapped_column(String(16), default="📦")  # standard emoji fallback
    emoji_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # premium custom_emoji_id
    duration_label: Mapped[str] = mapped_column(String(16), default="12m")
    price_usdt: Mapped[Decimal] = mapped_column(NUMERIC, default=Decimal("0"))
    description: Mapped[str] = mapped_column(Text, default="")
    delivery_type: Mapped[str] = mapped_column(String(16), default="stock_pool")
    api_handler: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class StockItem(Base):
    __tablename__ = "stock_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(Integer, ForeignKey("products.id"))
    payload: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="available")  # available|sold
    sold_to: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    sold_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    reserved_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    reserved_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    product_id: Mapped[int] = mapped_column(Integer, ForeignKey("products.id"))
    stock_item_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("stock_items.id"), nullable=True
    )
    price_usdt: Mapped[Decimal] = mapped_column(NUMERIC)
    payload_snapshot: Mapped[str] = mapped_column(Text, default="")  # delivered content snapshot
    status: Mapped[str] = mapped_column(String(16), default="completed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Withdrawal(Base):
    __tablename__ = "withdrawals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    amount_usdt: Mapped[Decimal] = mapped_column(NUMERIC)
    method: Mapped[str] = mapped_column(String(16))  # 'binance' | 'upi'
    address: Mapped[str] = mapped_column(String(255))  # binance UID or UPI id
    status: Mapped[str] = mapped_column(String(16), default="pending")
    admin_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


class ReferralEarning(Base):
    __tablename__ = "referral_earnings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    referrer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    source_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    source_event: Mapped[str] = mapped_column(String(32))  # source label
    source_ref_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    amount_usdt: Mapped[Decimal] = mapped_column(NUMERIC)
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(32))
    amount_usdt: Mapped[Decimal] = mapped_column(NUMERIC)
    ref_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DepositOrder(Base):
    """A user wallet top-up attempt, independent from shop purchases."""

    __tablename__ = "deposit_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    method: Mapped[str] = mapped_column(String(24), index=True)
    expected_amount: Mapped[Optional[Decimal]] = mapped_column(NUMERIC, nullable=True)
    received_amount: Mapped[Optional[Decimal]] = mapped_column(NUMERIC, nullable=True)
    currency: Mapped[str] = mapped_column(String(12), default="USDT")
    reference: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    external_transaction_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    rejection_code: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class BinanceOrder(Base):
    __tablename__ = "binance_orders"
    __table_args__ = (
        UniqueConstraint("order_id", name="uq_binance_orders_order_id"),
        UniqueConstraint("transaction_id", name="uq_binance_orders_transaction_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    deposit_order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("deposit_orders.id"), unique=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    order_id: Mapped[str] = mapped_column(String(128))
    transaction_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    amount: Mapped[Decimal] = mapped_column(NUMERIC)
    currency: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(24))
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_response: Mapped[str] = mapped_column(Text, default="")
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TxidVerification(Base):
    __tablename__ = "txid_verifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    deposit_order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("deposit_orders.id"), unique=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    txid: Mapped[str] = mapped_column(String(128), unique=True)
    block_number: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    confirmations: Mapped[int] = mapped_column(Integer, default=0)
    from_address: Mapped[str] = mapped_column(String(64), default="")
    to_address: Mapped[str] = mapped_column(String(64), default="")
    token_address: Mapped[str] = mapped_column(String(64), default="")
    amount: Mapped[Decimal] = mapped_column(NUMERIC)
    currency: Mapped[str] = mapped_column(String(12), default="USDT")
    status: Mapped[str] = mapped_column(String(24))
    raw_response: Mapped[str] = mapped_column(Text, default="")
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WalletTransaction(Base):
    """Idempotency ledger for wallet mutations made by the deposit subsystem."""

    __tablename__ = "wallet_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    deposit_order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("deposit_orders.id"), unique=True
    )
    kind: Mapped[str] = mapped_column(String(32), default="deposit")
    amount_usdt: Mapped[Decimal] = mapped_column(NUMERIC)
    balance_after: Mapped[Decimal] = mapped_column(NUMERIC)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResellerFulfillment(Base):
    """One non-retryable external supplier purchase per checkout attempt."""

    __tablename__ = "reseller_fulfillments"
    __table_args__ = (
        UniqueConstraint("request_key", name="uq_reseller_fulfillments_request_key"),
        UniqueConstraint("order_id", name="uq_reseller_fulfillments_order_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(Integer, ForeignKey("orders.id"))
    provider: Mapped[str] = mapped_column(String(32), default="canboso")
    request_key: Mapped[str] = mapped_column(String(160))
    vendor_product_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    vendor_order_code: Mapped[str] = mapped_column(String(128), default="")
    response_payload: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DepositReferenceClaim(Base):
    """Reference reservation created when an admin manually credits a deposit."""

    __tablename__ = "deposit_reference_claims"
    __table_args__ = (
        UniqueConstraint("provider", "reference", name="uq_deposit_reference_claims_provider_reference"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    deposit_order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("deposit_orders.id"), unique=True
    )
    provider: Mapped[str] = mapped_column(String(32), index=True)
    reference: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VerificationLog(Base):
    __tablename__ = "verification_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    deposit_order_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("deposit_orders.id"), nullable=True, index=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    provider: Mapped[str] = mapped_column(String(32))
    reference: Mapped[str] = mapped_column(String(128), default="")
    outcome: Mapped[str] = mapped_column(String(40), index=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    response_payload: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    token: Mapped[str] = mapped_column(String(64), unique=True)
    label: Mapped[str] = mapped_column(String(64), default="default")
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PaymentVerification(Base):
    __tablename__ = "payment_verifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    product_id: Mapped[int] = mapped_column(Integer, ForeignKey("products.id"))
    provider: Mapped[str] = mapped_column(String(32), default="binance_pay")
    reference: Mapped[str] = mapped_column(String(128))
    qty: Mapped[int] = mapped_column(Integer, default=1)
    expected_amount_usdt: Mapped[Decimal] = mapped_column(NUMERIC)
    received_amount_usdt: Mapped[Optional[Decimal]] = mapped_column(NUMERIC, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    verification_note: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    order_ids_csv: Mapped[str] = mapped_column(Text, default="")
    decided_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PaymentReferenceClaim(Base):
    """One-time claim for a shop payment reference, including pending checks."""

    __tablename__ = "payment_reference_claims"
    __table_args__ = (
        UniqueConstraint("provider", "reference", name="uq_payment_reference_claims_provider_reference"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    payment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("payment_verifications.id"), unique=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    reference: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Setting(Base):
    """Generic key/value runtime configuration (broadcasts, banners, etc.)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
